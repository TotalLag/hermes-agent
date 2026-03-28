#!/usr/bin/env python3
"""
Task Queue MCP Server - subprocess stdio-based JSON-RPC server.
Tracks task lifecycle: pending → assigned → running → completed / failed
Persisted to SQLite at ~/.hermes/hiclaw/task-queue.db
"""

from __future__ import annotations

import dataclasses
import datetime
from enum import Enum
import json
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

QUEUE_DIR = os.path.expanduser("~/.hermes/hiclaw/")
QUEUE_FILE = os.path.join(QUEUE_DIR, "task-queue.json")
DB_PATH = os.path.join(QUEUE_DIR, "task-queue.db")


class ManagerMode(Enum):
    IDLE = "idle"
    DISPATCHING = "dispatching"
    MONITORING = "monitoring"


VALID_TRANSITIONS = {
    "pending": {"assigned"},
    "assigned": {"running"},
    "running": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
}


@dataclass
class TaskInfo:
    id: str
    spec_path: str
    assigned_worker: str | None
    status: str
    created_at: str
    updated_at: str
    result_path: str | None
    error: str | None
    started_at: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _error(message: str, **extra: object) -> str:
    payload: dict[str, object] = {"status": "error", "message": message}
    payload.update(extra)
    return json.dumps(payload)


def _ok(**payload: object) -> str:
    return json.dumps(payload)


def _ensure_queue_dir() -> None:
    os.makedirs(QUEUE_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# SQLite support
# ---------------------------------------------------------------------------


def _get_db() -> sqlite3.Connection:
    """Return a WAL-mode SQLite connection. Creates schema if needed."""
    _ensure_queue_dir()
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id              TEXT PRIMARY KEY,
            spec_path       TEXT NOT NULL DEFAULT '',
            assigned_worker TEXT NOT NULL DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'pending',
            result_path     TEXT NOT NULL DEFAULT '',
            error           TEXT NOT NULL DEFAULT '',
            started_at      TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_assigned_worker ON tasks(assigned_worker);
        CREATE INDEX IF NOT EXISTS idx_tasks_updated_at ON tasks(updated_at);
        """
    )


# ---------------------------------------------------------------------------
# JSON → SQLite migration
# ---------------------------------------------------------------------------


def _migrate_json_to_sqlite() -> int:
    """Import existing JSON queue into SQLite. Returns count of tasks imported."""
    if not os.path.exists(QUEUE_FILE):
        return 0

    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return 0

    if not isinstance(data, dict):
        return 0

    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        return 0

    conn = _get_db()
    imported = 0
    for item in tasks:
        if not isinstance(item, dict):
            continue
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO tasks
                (id, spec_path, assigned_worker, status, result_path, error,
                 started_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(item.get("id", "")),
                    str(item.get("spec_path", "")),
                    str(item.get("assigned_worker") or ""),
                    str(item.get("status", "pending")),
                    str(item.get("result_path") or ""),
                    str(item.get("error") or ""),
                    str(item.get("started_at") or ""),
                    str(item.get("created_at", "")),
                    str(item.get("updated_at", "")),
                ),
            )
            if conn.total_changes > 0:
                imported += 1
        except Exception as exc:
            logger.warning("Skipping invalid task during migration: %s", exc)

    conn.commit()
    conn.close()

    if imported > 0:
        bak_path = QUEUE_FILE + ".migrated"
        os.replace(QUEUE_FILE, bak_path)
        logger.warning(
            "Migrated %d tasks from JSON to SQLite. Backup: %s", imported, bak_path
        )

    return imported


# Ensure migration runs once at module load
_migrate_json_to_sqlite()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task_from_row(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a task dict."""
    return {
        "id": row["id"],
        "spec_path": row["spec_path"],
        "assigned_worker": row["assigned_worker"] or None,
        "status": row["status"],
        "result_path": row["result_path"] or None,
        "error": row["error"] or None,
        "started_at": row["started_at"] or None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _task_to_dict(task: TaskInfo) -> dict:
    """Serialize TaskInfo dataclass to dict."""
    return {
        "id": task.id,
        "spec_path": task.spec_path,
        "assigned_worker": task.assigned_worker,
        "status": task.status,
        "result_path": task.result_path,
        "error": task.error,
        "started_at": task.started_at,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def _validate_status_transition(current: str, new: str) -> None:
    """Raise ValueError if transition is not allowed."""
    if current == new:
        return  # No-op is always allowed
    allowed = VALID_TRANSITIONS.get(current, set())
    if new not in allowed:
        raise ValueError(
            f"Invalid status transition: {current} → {new}. "
            f"Allowed from '{current}': {', '.join(sorted(allowed)) or 'none'}"
        )


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def tq_add_task(task_id: str, spec_path: str) -> str:
    """Add a new task in pending state."""
    if not isinstance(task_id, str) or not task_id.strip():
        return _error("task_id is required")
    if not isinstance(spec_path, str) or not spec_path.strip():
        return _error("spec_path is required")

    now = _utc_now()
    conn = _get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if existing is not None:
            return _error(f"Task '{task_id}' already exists")

        conn.execute(
            """
            INSERT INTO tasks (id, spec_path, status, created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?)
            """,
            (task_id.strip(), spec_path.strip(), now, now),
        )
        conn.commit()
    finally:
        conn.close()

    task = TaskInfo(
        id=task_id.strip(),
        spec_path=spec_path.strip(),
        assigned_worker=None,
        status="pending",
        created_at=now,
        updated_at=now,
        result_path=None,
        error=None,
        started_at=None,
    )
    return _ok(success=True, task=_task_to_dict(task))


WORKER_REGISTRY_DB = os.path.join(QUEUE_DIR, "workers-registry.db")


def tq_assign(task_id: str, worker_id: str) -> str:
    """Assign a pending task to a worker. Atomic: updates both task and worker status."""
    if not isinstance(task_id, str) or not task_id.strip():
        return _error("task_id is required")
    if not isinstance(worker_id, str) or not worker_id.strip():
        return _error("worker_id is required")

    now = _utc_now()
    conn = _get_db()
    try:
        worker_db_exists = os.path.exists(WORKER_REGISTRY_DB)
        if worker_db_exists:
            conn.execute(f"ATTACH DATABASE ? AS wr", (WORKER_REGISTRY_DB,))

        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return _error(f"Task '{task_id}' not found")

        current_status = row["status"]
        if current_status != "pending":
            return _error(
                f"Cannot assign task in '{current_status}' status. Must be 'pending'"
            )

        task_updated = conn.execute(
            """
            UPDATE tasks SET assigned_worker=?, status='assigned', updated_at=?
            WHERE id=? AND status='pending'
            """,
            (worker_id.strip(), now, task_id),
        )
        if task_updated.rowcount == 0:
            conn.rollback()
            return _error(f"Task '{task_id}' not available for assignment")

        if worker_db_exists:
            worker_updated = conn.execute(
                """
                UPDATE wr.workers SET status='busy', last_status_change=?
                WHERE worker_id=? AND status='ready'
                """,
                (now, worker_id.strip()),
            )
            if worker_updated.rowcount == 0:
                conn.rollback()
                conn.execute("DETACH DATABASE wr")
                return _error(
                    f"Worker '{worker_id}' is not available (not in 'ready' status)"
                )

        conn.commit()
        if worker_db_exists:
            conn.execute("DETACH DATABASE wr")

        updated_row = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return _ok(success=True, task=_task_from_row(updated_row))
    except Exception:
        try:
            conn.rollback()
            conn.execute("DETACH DATABASE wr")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def tq_start(task_id: str) -> str:
    """Mark an assigned task as running."""
    if not isinstance(task_id, str) or not task_id.strip():
        return _error("task_id is required")

    now = _utc_now()
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return _error(f"Task '{task_id}' not found")

        current_status = row["status"]
        if current_status != "assigned":
            return _error(
                f"Cannot start task in '{current_status}' status. Must be 'assigned'"
            )

        conn.execute(
            """
            UPDATE tasks SET status='running', started_at=?, updated_at=?
            WHERE id=?
            """,
            (now, now, task_id),
        )
        conn.commit()

        updated_row = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return _ok(success=True, task=_task_from_row(updated_row))
    finally:
        conn.close()


def tq_complete(task_id: str, result_path: str) -> str:
    """Mark a running task as completed. Frees the worker back to ready status."""
    if not isinstance(task_id, str) or not task_id.strip():
        return _error("task_id is required")
    if not isinstance(result_path, str):
        return _error("result_path is required")

    now = _utc_now()
    conn = _get_db()
    try:
        worker_db_exists = os.path.exists(WORKER_REGISTRY_DB)
        if worker_db_exists:
            conn.execute(f"ATTACH DATABASE ? AS wr", (WORKER_REGISTRY_DB,))

        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return _error(f"Task '{task_id}' not found")

        current_status = row["status"]
        if current_status != "running":
            return _error(
                f"Cannot complete task in '{current_status}' status. Must be 'running'"
            )

        # Capture assigned_worker before clearing it
        assigned_worker = row["assigned_worker"]

        conn.execute(
            """
            UPDATE tasks SET status='completed', result_path=?, assigned_worker='',
                             updated_at=?
            WHERE id=?
            """,
            (result_path, now, task_id),
        )

        if worker_db_exists and assigned_worker:
            conn.execute(
                """
                UPDATE wr.workers SET status='ready', last_status_change=?
                WHERE worker_id=? AND status='busy'
                """,
                (now, assigned_worker),
            )

        conn.commit()
        if worker_db_exists:
            conn.execute("DETACH DATABASE wr")

        updated_row = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return _ok(success=True, task=_task_from_row(updated_row))
    except Exception:
        try:
            conn.rollback()
            conn.execute("DETACH DATABASE wr")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def tq_fail(task_id: str, error: str) -> str:
    """Mark a running task as failed. Frees the worker back to ready status."""
    if not isinstance(task_id, str) or not task_id.strip():
        return _error("task_id is required")
    if not isinstance(error, str):
        return _error("error must be a string")

    now = _utc_now()
    conn = _get_db()
    try:
        worker_db_exists = os.path.exists(WORKER_REGISTRY_DB)
        if worker_db_exists:
            conn.execute(f"ATTACH DATABASE ? AS wr", (WORKER_REGISTRY_DB,))

        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return _error(f"Task '{task_id}' not found")

        current_status = row["status"]
        if current_status not in ("running", "failed"):
            return _error(
                f"Cannot fail task in '{current_status}' status. Must be 'running' or 'failed'"
            )

        assigned_worker = row["assigned_worker"]

        conn.execute(
            """
            UPDATE tasks SET status='failed', error=?, assigned_worker='',
                             updated_at=?
            WHERE id=?
            """,
            (error, now, task_id),
        )

        if worker_db_exists and assigned_worker:
            conn.execute(
                """
                UPDATE wr.workers SET status='ready', last_status_change=?
                WHERE worker_id=? AND status='busy'
                """,
                (now, assigned_worker),
            )

        conn.commit()
        if worker_db_exists:
            conn.execute("DETACH DATABASE wr")

        updated_row = conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return _ok(success=True, task=_task_from_row(updated_row))
    except Exception:
        try:
            conn.rollback()
            conn.execute("DETACH DATABASE wr")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def tq_fail_stale_tasks(timeout_seconds: int) -> str:
    """Mark running tasks past timeout as failed with error 'Task timed out'."""
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        return _error("timeout_seconds must be a positive integer")

    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
    cutoff_str = cutoff.isoformat()

    conn = _get_db()
    try:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'running' AND updated_at != '' AND updated_at < ?
            """,
            (cutoff_str,),
        ).fetchall()

        if rows:
            stale_ids = [row["id"] for row in rows]
            stale_workers = {
                row["id"]: row["assigned_worker"]
                for row in rows
                if row["assigned_worker"]
            }
            now = _utc_now()

            worker_db_exists = os.path.exists(WORKER_REGISTRY_DB)
            if worker_db_exists:
                conn.execute(f"ATTACH DATABASE ? AS wr", (WORKER_REGISTRY_DB,))

            conn.executemany(
                """
                UPDATE tasks SET status='failed', error='Task timed out',
                                 assigned_worker='', updated_at=?
                WHERE id=?
                """,
                [(now, tid) for tid in stale_ids],
            )

            if worker_db_exists and stale_workers:
                for tid, wid in stale_workers.items():
                    conn.execute(
                        """
                        UPDATE wr.workers SET status='ready', last_status_change=?
                        WHERE worker_id=? AND status='busy'
                        """,
                        (now, wid),
                    )

            conn.commit()
            if worker_db_exists:
                conn.execute("DETACH DATABASE wr")

        stale_tasks = [_task_from_row(row) for row in rows]
        return _ok(
            status="ok",
            count=len(stale_tasks),
            timeout_seconds=timeout_seconds,
            cutoff=cutoff_str,
            tasks=stale_tasks,
        )
    finally:
        conn.close()


def tq_list(status: str | None = None) -> str:
    """List tasks, optionally filtered by status."""
    conn = _get_db()
    try:
        if status is not None and str(status).strip() != "":
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = ?", (status.strip(),)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tasks").fetchall()

        tasks = [_task_from_row(row) for row in rows]
        return json.dumps({"tasks": tasks, "count": len(tasks)})
    finally:
        conn.close()


def tq_get(task_id: str) -> str:
    """Get a specific task by ID."""
    if not isinstance(task_id, str) or not task_id.strip():
        return _error("task_id is required")

    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return _error(f"Task '{task_id}' not found")
        return json.dumps({"task": _task_from_row(row)})
    finally:
        conn.close()


def tq_stats() -> str:
    """Get task queue statistics."""
    conn = _get_db()
    try:
        rows = conn.execute("SELECT status FROM tasks").fetchall()
        tasks = [row["status"] for row in rows]
        total = len(tasks)
        completed = sum(1 for t in tasks if t == "completed")
        failed = sum(1 for t in tasks if t == "failed")
        pending = sum(1 for t in tasks if t == "pending")
        assigned = sum(1 for t in tasks if t == "assigned")
        running = sum(1 for t in tasks if t == "running")
        return json.dumps(
            {
                "total_tasks": total,
                "completed_tasks": completed,
                "failed_tasks": failed,
                "pending_tasks": pending,
                "assigned_tasks": assigned,
                "running_tasks": running,
            }
        )
    finally:
        conn.close()


def tq_set_mode(mode: str) -> str:
    """Set the manager mode (persisted to DB)."""
    try:
        ManagerMode(mode)
    except ValueError:
        return _error(
            f"Invalid mode '{mode}'. Must be one of: idle, dispatching, monitoring"
        )

    if not isinstance(mode, str):
        return _error("mode must be a string")

    conn = _get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('mode', ?)",
            (mode.strip(),),
        )
        conn.commit()
        return _ok(success=True, mode=mode.strip())
    finally:
        conn.close()


def tq_get_mode() -> str:
    """Get the current manager mode."""
    conn = _get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)
            """
        )
        row = conn.execute("SELECT value FROM meta WHERE key = 'mode'").fetchone()
        mode = row["value"] if row else ManagerMode.IDLE.value
        return json.dumps({"mode": mode})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# MCP tool definitions
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "tq_add_task",
        "description": "Add a new task to the queue in pending state",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Unique task identifier"},
                "spec_path": {
                    "type": "string",
                    "description": "Path to task specification",
                },
            },
            "required": ["task_id", "spec_path"],
        },
    },
    {
        "name": "tq_assign",
        "description": "Assign a pending task to a worker",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task identifier"},
                "worker_id": {"type": "string", "description": "Worker identifier"},
            },
            "required": ["task_id", "worker_id"],
        },
    },
    {
        "name": "tq_start",
        "description": "Mark an assigned task as running",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task identifier"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "tq_complete",
        "description": "Mark a running task as completed with result path",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task identifier"},
                "result_path": {"type": "string", "description": "Path to task result"},
            },
            "required": ["task_id", "result_path"],
        },
    },
    {
        "name": "tq_fail",
        "description": "Mark a running task as failed with error message",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task identifier"},
                "error": {"type": "string", "description": "Error message"},
            },
            "required": ["task_id", "error"],
        },
    },
    {
        "name": "tq_fail_stale_tasks",
        "description": (
            "Mark running tasks that have not been updated within timeout_seconds as failed. "
            "Useful for cleaning up tasks whose workers died."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Seconds since last update to consider a running task stale.",
                },
            },
            "required": ["timeout_seconds"],
        },
    },
    {
        "name": "tq_list",
        "description": "List tasks, optionally filtered by status",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status (pending/assigned/running/completed/failed)",
                },
            },
        },
    },
    {
        "name": "tq_get",
        "description": "Get a specific task by ID",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task identifier"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "tq_stats",
        "description": "Get task queue statistics",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "tq_set_mode",
        "description": "Set the manager mode",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["idle", "dispatching", "monitoring"],
                    "description": "Manager mode",
                },
            },
            "required": ["mode"],
        },
    },
    {
        "name": "tq_get_mode",
        "description": "Get the current manager mode",
        "input_schema": {"type": "object", "properties": {}},
    },
]


TOOL_HANDLERS = {
    "tq_add_task": tq_add_task,
    "tq_assign": tq_assign,
    "tq_start": tq_start,
    "tq_complete": tq_complete,
    "tq_fail": tq_fail,
    "tq_fail_stale_tasks": tq_fail_stale_tasks,
    "tq_list": tq_list,
    "tq_get": tq_get,
    "tq_stats": tq_stats,
    "tq_set_mode": tq_set_mode,
    "tq_get_mode": tq_get_mode,
}


def handle_tool_call(tool_name: str, args: dict) -> str:
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return _error(f"Unknown tool: {tool_name}")

    try:
        return handler(**args)
    except TypeError as exc:
        logger.warning("Invalid arguments for %s: %s", tool_name, exc)
        return _error(f"Invalid arguments for {tool_name}: {exc}")
    except Exception as exc:
        logger.exception("Tool %s raised: %s", tool_name, exc)
        return _error(str(exc))


def write_json(obj):
    print(json.dumps(obj), flush=True)


def main():
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    # Prime the DB connection (runs migration) at startup
    _get_db().close()
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            request = json.loads(line)
            method = request.get("method", "")
            if method == "initialize":
                write_json(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "serverInfo": {
                                "name": "task-queue",
                                "version": "1.1.0",
                            },
                        },
                    }
                )
            elif method == "tools/list":
                write_json(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "result": {"tools": TOOL_DEFINITIONS},
                    }
                )
            elif method == "tools/call":
                result = handle_tool_call(
                    request["params"]["name"], request["params"].get("arguments", {})
                )
                write_json(
                    {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "result": {"content": [{"type": "text", "text": result}]},
                    }
                )
        except Exception as e:
            write_json({"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    main()
