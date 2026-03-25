from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REGISTRY_DIR = os.path.expanduser("~/.hermes/hiclaw/")
REGISTRY_PATH = os.path.join(REGISTRY_DIR, "workers-registry.json")
DB_PATH = os.path.join(REGISTRY_DIR, "workers-registry.db")

VALID_STATUSES = {"registered", "ready", "busy", "done", "error", "offline"}
STALE_THRESHOLD_SECONDS = 300

# Status transition graph: key status → set of allowed next statuses
STATUS_TRANSITIONS = {
    "registered": {"ready", "offline", "error"},
    "ready": {"busy", "offline", "error"},
    "busy": {"ready", "offline", "error"},
    "done": {"ready", "offline", "error"},
    "error": {"ready", "offline"},
    "offline": {"ready", "error"},
}


@dataclass
class Worker:
    worker_id: str
    name: str
    capabilities: list[str]
    status: str
    version: str
    matrix_user_id: str
    device_id: str
    room_id: str = ""
    registered_at: str = ""
    last_seen_at: str = ""
    last_status_change: str = ""
    metadata: dict = field(default_factory=dict)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _error(message: str, **extra: object) -> str:
    payload: dict[str, object] = {"status": "error", "message": message}
    payload.update(extra)
    return json.dumps(payload)


def _ok(**payload: object) -> str:
    return json.dumps(payload)


def _ensure_registry_dir() -> None:
    os.makedirs(REGISTRY_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# SQLite support
# ---------------------------------------------------------------------------


def _get_db() -> sqlite3.Connection:
    """Return a WAL-mode SQLite connection. Creates schema if needed."""
    _ensure_registry_dir()
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS workers (
            worker_id       TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            capabilities    TEXT NOT NULL DEFAULT '[]',
            version         TEXT NOT NULL DEFAULT '1.0.0',
            matrix_user_id  TEXT NOT NULL,
            device_id       TEXT NOT NULL DEFAULT '',
            room_id         TEXT NOT NULL DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'registered',
            message         TEXT NOT NULL DEFAULT '',
            last_seen_at    TEXT NOT NULL DEFAULT '',
            last_status_change TEXT NOT NULL DEFAULT '',
            registered_at   TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status);
        CREATE INDEX IF NOT EXISTS idx_workers_last_seen ON workers(last_seen_at);
        """
    )


# ---------------------------------------------------------------------------
# JSON → SQLite migration
# ---------------------------------------------------------------------------


def _migrate_json_to_sqlite() -> int:
    """Import existing JSON registry into SQLite. Returns count of workers imported."""
    if not os.path.exists(REGISTRY_PATH):
        return 0

    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return 0

    if not isinstance(data, list):
        return 0

    conn = _get_db()
    imported = 0
    for item in data:
        try:
            worker = _worker_from_dict(item)
            conn.execute(
                """
                INSERT OR IGNORE INTO workers
                (worker_id, name, capabilities, version, matrix_user_id, device_id,
                 room_id, status, message, last_seen_at, last_status_change, registered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    worker.worker_id,
                    worker.name,
                    json.dumps(worker.capabilities),
                    worker.version,
                    worker.matrix_user_id,
                    worker.device_id,
                    worker.room_id,
                    worker.status,
                    worker.metadata.get("message", ""),
                    worker.last_seen_at,
                    worker.last_status_change or _utc_now(),
                    worker.registered_at,
                ),
            )
            if conn.total_changes > 0:
                imported += 1
        except Exception as exc:
            logger.warning("Skipping invalid worker during migration: %s", exc)

    conn.commit()
    conn.close()

    if imported > 0:
        bak_path = REGISTRY_PATH + ".migrated"
        os.replace(REGISTRY_PATH, bak_path)
        logger.warning(
            "Migrated %d workers from JSON to SQLite. Backup: %s", imported, bak_path
        )

    return imported


# Ensure migration runs once at module load
_migrate_json_to_sqlite()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_capabilities(capabilities: object) -> list[str]:
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) for item in capabilities
    ):
        raise ValueError("capabilities must be a list of strings")
    return capabilities


def _worker_from_row(row: sqlite3.Row) -> Worker:
    """Convert a sqlite3.Row to a Worker dataclass."""
    capabilities_raw = row["capabilities"]
    capabilities = (
        json.loads(capabilities_raw)
        if isinstance(capabilities_raw, str)
        else capabilities_raw or []
    )
    metadata: dict[str, Any] = {}
    if row["message"]:
        metadata["message"] = row["message"]
    return Worker(
        worker_id=row["worker_id"],
        name=row["name"],
        capabilities=capabilities,
        status=row["status"],
        version=row["version"],
        matrix_user_id=row["matrix_user_id"],
        device_id=row["device_id"],
        room_id=row["room_id"],
        registered_at=row["registered_at"],
        last_seen_at=row["last_seen_at"],
        last_status_change=row["last_status_change"],
        metadata=metadata,
    )


def _worker_from_dict(data: dict) -> Worker:
    if not isinstance(data, dict):
        raise ValueError("worker record must be an object")

    raw_metadata = data.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}

    worker = Worker(
        worker_id=str(data.get("worker_id", "")),
        name=str(data.get("name", "")),
        capabilities=_validate_capabilities(data.get("capabilities", [])),
        status=str(data.get("status", "")),
        version=str(data.get("version", "")),
        matrix_user_id=str(data.get("matrix_user_id", "")),
        device_id=str(data.get("device_id", "")),
        room_id=str(data.get("room_id", "")),
        registered_at=str(data.get("registered_at", "")),
        last_seen_at=str(data.get("last_seen_at", "")),
        last_status_change=str(data.get("last_status_change", "")),
        metadata=metadata,
    )

    if not worker.worker_id:
        raise ValueError("worker_id is required")
    if not worker.name:
        raise ValueError(f"worker {worker.worker_id}: name is required")
    if worker.status not in VALID_STATUSES:
        raise ValueError(
            f"worker {worker.worker_id}: invalid status '{worker.status or '<empty>'}'"
        )
    if not worker.version:
        raise ValueError(f"worker {worker.worker_id}: version is required")
    if not worker.matrix_user_id:
        raise ValueError(f"worker {worker.worker_id}: matrix_user_id is required")
    if not worker.device_id:
        raise ValueError(f"worker {worker.worker_id}: device_id is required")

    return worker


def _validate_status(status: str) -> str:
    if not isinstance(status, str) or not status.strip():
        raise ValueError("status is required")
    normalized = status.strip()
    if normalized not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status '{normalized}'. Valid: {', '.join(sorted(VALID_STATUSES))}"
        )
    return normalized


def _validate_status_transition(current: str, new: str) -> None:
    """Raise ValueError if transition is not allowed."""
    if current == new:
        return  # No-op is always allowed
    allowed = STATUS_TRANSITIONS.get(current, set())
    if new not in allowed:
        raise ValueError(
            f"Invalid status transition: {current} → {new}. "
            f"Allowed from '{current}': {', '.join(sorted(allowed)) or 'none'}"
        )


def _require_worker_id(worker_id: str) -> None:
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValueError("worker_id is required")


def _worker_to_dict(worker: Worker) -> dict:
    """Serialize Worker dataclass to dict matching the old JSON format."""
    return {
        "worker_id": worker.worker_id,
        "name": worker.name,
        "capabilities": worker.capabilities,
        "status": worker.status,
        "version": worker.version,
        "matrix_user_id": worker.matrix_user_id,
        "device_id": worker.device_id,
        "room_id": worker.room_id,
        "registered_at": worker.registered_at,
        "last_seen_at": worker.last_seen_at,
        "last_status_change": worker.last_status_change,
        "metadata": worker.metadata,
    }


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def wr_register(
    worker_id: str,
    name: str,
    capabilities: list[str],
    version: str,
    matrix_user_id: str,
    device_id: str,
    room_id: str = "",
) -> str:
    try:
        _require_worker_id(worker_id)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name is required")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version is required")
        if not isinstance(matrix_user_id, str) or not matrix_user_id.strip():
            raise ValueError("matrix_user_id is required")
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("device_id is required")
        validated_capabilities = _validate_capabilities(capabilities)
    except ValueError as exc:
        return _error(str(exc))

    now = _utc_now()
    conn = _get_db()
    try:
        existing = conn.execute(
            "SELECT worker_id FROM workers WHERE worker_id = ?", (worker_id,)
        ).fetchone()
        if existing is not None:
            logger.warning("Duplicate worker registration rejected for %s", worker_id)
            return _error(
                f"Worker {worker_id} already registered. Use wr_heartbeat instead.",
                worker_id=worker_id,
            )

        conn.execute(
            """
            INSERT INTO workers
            (worker_id, name, capabilities, version, matrix_user_id, device_id,
             room_id, status, last_seen_at, last_status_change, registered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'registered', ?, ?, ?)
            """,
            (
                worker_id,
                name.strip(),
                json.dumps(validated_capabilities),
                version.strip(),
                matrix_user_id.strip(),
                device_id.strip(),
                room_id.strip() if isinstance(room_id, str) else "",
                now,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    worker = Worker(
        worker_id=worker_id,
        name=name.strip(),
        capabilities=validated_capabilities,
        status="registered",
        version=version.strip(),
        matrix_user_id=matrix_user_id.strip(),
        device_id=device_id.strip(),
        room_id=room_id.strip() if isinstance(room_id, str) else "",
        registered_at=now,
        last_seen_at=now,
        last_status_change=now,
        metadata={},
    )
    return _ok(status="ok", worker=_worker_to_dict(worker))


def wr_heartbeat(worker_id: str) -> str:
    try:
        _require_worker_id(worker_id)
    except ValueError as exc:
        return _error(str(exc))

    now = _utc_now()
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM workers WHERE worker_id = ?", (worker_id,)
        ).fetchone()
        if row is None:
            logger.warning("Heartbeat received for missing worker %s", worker_id)
            return _error(f"Worker {worker_id} not found.", worker_id=worker_id)

        current_status = row["status"]
        new_status = current_status

        # Auto-recover offline workers to ready
        if current_status == "offline":
            new_status = "ready"
            conn.execute(
                "UPDATE workers SET last_seen_at=?, status=?, last_status_change=? "
                "WHERE worker_id=?",
                (now, new_status, now, worker_id),
            )
        else:
            conn.execute(
                "UPDATE workers SET last_seen_at=? WHERE worker_id=?",
                (now, worker_id),
            )

        conn.commit()
        return _ok(
            status="ok",
            worker_id=worker_id,
            last_seen_at=now,
            worker_status=new_status,
        )
    finally:
        conn.close()


def wr_update_status(worker_id: str, status: str, message: str = "") -> str:
    try:
        _require_worker_id(worker_id)
        normalized_status = _validate_status(status)
        if message is not None and not isinstance(message, str):
            raise ValueError("message must be a string")
    except ValueError as exc:
        return _error(str(exc))

    now = _utc_now()
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM workers WHERE worker_id = ?", (worker_id,)
        ).fetchone()
        if row is None:
            logger.warning("Status update received for missing worker %s", worker_id)
            return _error(f"Worker {worker_id} not found.", worker_id=worker_id)

        current_status = row["status"]
        _validate_status_transition(current_status, normalized_status)

        conn.execute(
            "UPDATE workers SET status=?, message=?, last_seen_at=?, last_status_change=? "
            "WHERE worker_id=?",
            (
                normalized_status,
                message or "",
                now,
                now,
                worker_id,
            ),
        )
        conn.commit()
        return _ok(
            status="ok",
            worker_id=worker_id,
            worker_status=normalized_status,
            last_seen_at=now,
            message=message or "",
        )
    except ValueError as exc:
        return _error(str(exc))
    finally:
        conn.close()


def wr_remove(worker_id: str) -> str:
    try:
        _require_worker_id(worker_id)
    except ValueError as exc:
        return _error(str(exc))

    conn = _get_db()
    try:
        affected = conn.execute(
            "DELETE FROM workers WHERE worker_id = ?", (worker_id,)
        ).rowcount
        conn.commit()
        if affected == 0:
            logger.warning("Remove requested for missing worker %s", worker_id)
            return _error(f"Worker {worker_id} not found.", worker_id=worker_id)
        return _ok(status="ok", removed=True, worker_id=worker_id)
    finally:
        conn.close()


def wr_list(status: str | None = None) -> str:
    try:
        normalized_status = None
        if status is not None and str(status).strip() != "":
            normalized_status = _validate_status(str(status))
    except ValueError as exc:
        return _error(str(exc))

    conn = _get_db()
    try:
        if normalized_status is not None:
            rows = conn.execute(
                "SELECT * FROM workers WHERE status = ?", (normalized_status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM workers").fetchall()

        workers = [_worker_to_dict(_worker_from_row(row)) for row in rows]
        return _ok(status="ok", count=len(workers), workers=workers)
    finally:
        conn.close()


def wr_get(worker_id: str) -> str:
    try:
        _require_worker_id(worker_id)
    except ValueError as exc:
        return _error(str(exc))

    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM workers WHERE worker_id = ?", (worker_id,)
        ).fetchone()
        if row is None:
            logger.warning("Lookup requested for missing worker %s", worker_id)
            return _error(f"Worker {worker_id} not found.", worker_id=worker_id)
        return _ok(status="ok", worker=_worker_to_dict(_worker_from_row(row)))
    finally:
        conn.close()


def wr_get_stale_workers(timeout_seconds: int = STALE_THRESHOLD_SECONDS) -> str:
    """Return workers that have not sent a heartbeat within timeout_seconds.

    Also marks them as offline. Workers that are already offline are excluded.
    """
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        return _error("timeout_seconds must be a positive integer")

    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
    cutoff_str = cutoff.isoformat()

    conn = _get_db()
    try:
        # Pick up non-offline workers who haven't been seen since cutoff
        rows = conn.execute(
            """
            SELECT * FROM workers
            WHERE status != 'offline' AND last_seen_at != '' AND last_seen_at < ?
            """,
            (cutoff_str,),
        ).fetchall()

        if rows:
            stale_ids = [row["worker_id"] for row in rows]
            conn.executemany(
                "UPDATE workers SET status='offline', last_status_change=? "
                "WHERE worker_id=?",
                [(_utc_now(), wid) for wid in stale_ids],
            )
            conn.commit()

        stale_workers = [_worker_to_dict(_worker_from_row(row)) for row in rows]
        return _ok(
            status="ok",
            count=len(stale_workers),
            timeout_seconds=timeout_seconds,
            cutoff=cutoff_str,
            workers=stale_workers,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# MCP tool definitions
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "wr_register",
        "description": "Register a new worker in the persistent worker registry.",
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Unique worker ID."},
                "name": {
                    "type": "string",
                    "description": "Human-readable worker name.",
                },
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Capabilities supported by this worker.",
                },
                "version": {"type": "string", "description": "Worker version."},
                "matrix_user_id": {
                    "type": "string",
                    "description": "Matrix user ID used by the worker.",
                },
                "device_id": {
                    "type": "string",
                    "description": "Matrix device ID used by the worker.",
                },
                "room_id": {
                    "type": "string",
                    "description": "Optional Matrix room ID for coordination.",
                },
            },
            "required": [
                "worker_id",
                "name",
                "capabilities",
                "version",
                "matrix_user_id",
                "device_id",
            ],
        },
    },
    {
        "name": "wr_heartbeat",
        "description": (
            "Refresh a worker's last_seen_at timestamp. "
            "Automatically recovers offline workers back to 'ready'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Worker ID."}
            },
            "required": ["worker_id"],
        },
    },
    {
        "name": "wr_update_status",
        "description": (
            "Update a worker's status and optional status message. "
            "Valid transitions: registered→ready/offline/error, "
            "ready→busy/offline/error, busy→ready/offline/error, "
            "done→ready/offline/error, error→ready/offline, "
            "offline→ready/error."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Worker ID."},
                "status": {
                    "type": "string",
                    "description": "New status: registered, ready, busy, done, error, offline.",
                },
                "message": {
                    "type": "string",
                    "description": "Optional status message stored under metadata.message.",
                },
            },
            "required": ["worker_id", "status"],
        },
    },
    {
        "name": "wr_remove",
        "description": "Remove a worker from the registry.",
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Worker ID."}
            },
            "required": ["worker_id"],
        },
    },
    {
        "name": "wr_list",
        "description": "List all workers, optionally filtered by status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional status filter. Empty or omitted returns all workers.",
                }
            },
        },
    },
    {
        "name": "wr_get",
        "description": "Get a specific worker from the registry.",
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Worker ID."}
            },
            "required": ["worker_id"],
        },
    },
    {
        "name": "wr_get_stale_workers",
        "description": (
            "Return workers that have not sent a heartbeat within timeout_seconds. "
            "Marks stale workers as offline. "
            "Call periodically (e.g., every 60s) to keep worker state accurate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "timeout_seconds": {
                    "type": "integer",
                    "description": f"Seconds since last heartbeat to consider stale. Default: {STALE_THRESHOLD_SECONDS}.",
                    "default": STALE_THRESHOLD_SECONDS,
                }
            },
        },
    },
]


TOOL_HANDLERS = {
    "wr_register": wr_register,
    "wr_heartbeat": wr_heartbeat,
    "wr_update_status": wr_update_status,
    "wr_remove": wr_remove,
    "wr_list": wr_list,
    "wr_get": wr_get,
    "wr_get_stale_workers": wr_get_stale_workers,
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
                                "name": "worker-registry",
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
