#!/usr/bin/env python3
"""
Task Queue MCP Server - subprocess stdio-based JSON-RPC server.
Tracks task lifecycle: pending → assigned → running → completed / failed
Persisted to ~/.hermes/hiclaw/task-queue.json
"""

import dataclasses
import datetime
import enum
import json
import logging
import os
import sys
from pathlib import Path

# ============================================================================
# Dataclasses & Enums
# ============================================================================


class ManagerMode(Enum):
    IDLE = "idle"
    DISPATCHING = "dispatching"
    MONITORING = "monitoring"


@dataclass
class TaskInfo:
    id: str
    spec_path: str
    assigned_worker: str | None
    status: str  # pending, assigned, running, completed, failed
    created_at: str  # ISO timestamp
    updated_at: str  # ISO timestamp
    result_path: str | None
    error: str | None


# ============================================================================
# Constants
# ============================================================================

QUEUE_FILE = Path.home() / ".hermes" / "hiclaw" / "task-queue.json"

VALID_TRANSITIONS = {
    "pending": ["assigned"],
    "assigned": ["running"],
    "running": ["completed", "failed"],
    "completed": [],
    "failed": [],
}

TOOL_DEFINITIONS = [
    {
        "name": "tq_add_task",
        "description": "Add a new task to the queue in pending state",
        "inputSchema": {
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
        "inputSchema": {
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
        "inputSchema": {
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
        "inputSchema": {
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
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task identifier"},
                "error": {"type": "string", "description": "Error message"},
            },
            "required": ["task_id", "error"],
        },
    },
    {
        "name": "tq_list",
        "description": "List tasks, optionally filtered by status",
        "inputSchema": {
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
        "inputSchema": {
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
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tq_set_mode",
        "description": "Set the manager mode",
        "inputSchema": {
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
        "inputSchema": {"type": "object", "properties": {}},
    },
]

# ============================================================================
# State Helpers
# ============================================================================


def _now_iso() -> str:
    """Return current UTC time in ISO format."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _load_queue() -> dict:
    """Load queue state from file, returns default if not exists."""
    if not QUEUE_FILE.exists():
        return {"tasks": [], "mode": ManagerMode.IDLE.value}
    try:
        with open(QUEUE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"tasks": [], "mode": ManagerMode.IDLE.value}


def _save_queue(data: dict) -> None:
    """Atomically save queue state to file."""
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = QUEUE_FILE.with_suffix(".tmp")
    with open(temp_file, "w") as f:
        json.dump(data, f, indent=2)
    temp_file.replace(QUEUE_FILE)


def _find_task(task_id: str) -> dict | None:
    """Find task by ID, returns None if not found."""
    data = _load_queue()
    for task in data.get("tasks", []):
        if task["id"] == task_id:
            return task
    return None


def _update_task(task_id: str, updates: dict) -> dict | None:
    """Update task fields and save, returns updated task or None if not found."""
    data = _load_queue()
    for i, task in enumerate(data["tasks"]):
        if task["id"] == task_id:
            data["tasks"][i].update(updates)
            data["tasks"][i]["updated_at"] = _now_iso()
            _save_queue(data)
            return data["tasks"][i]
    return None


def _validate_status_transition(current: str, new: str) -> bool:
    """Check if status transition is valid."""
    return new in VALID_TRANSITIONS.get(current, [])


# ============================================================================
# Tool Handlers
# ============================================================================

TOOL_HANDLERS = {}


def _register_handler(name: str):
    """Decorator to register a tool handler."""

    def decorator(func):
        TOOL_HANDLERS[name] = func
        return func

    return decorator


@_register_handler("tq_add_task")
def tq_add_task(task_id: str, spec_path: str) -> str:
    """Add a new task in pending state."""
    data = _load_queue()
    for task in data["tasks"]:
        if task["id"] == task_id:
            return json.dumps({"error": f"Task '{task_id}' already exists"})

    now = _now_iso()
    task = {
        "id": task_id,
        "spec_path": spec_path,
        "assigned_worker": None,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "result_path": None,
        "error": None,
    }
    data["tasks"].append(task)
    _save_queue(data)
    return json.dumps({"success": True, "task": task})


@_register_handler("tq_assign")
def tq_assign(task_id: str, worker_id: str) -> str:
    """Assign a pending task to a worker."""
    task = _find_task(task_id)
    if task is None:
        return json.dumps({"error": f"Task '{task_id}' not found"})
    if task["status"] != "pending":
        return json.dumps(
            {
                "error": f"Cannot assign task in '{task['status']}' status. Must be 'pending'"
            }
        )

    updated = _update_task(
        task_id,
        {
            "assigned_worker": worker_id,
            "status": "assigned",
        },
    )
    return json.dumps({"success": True, "task": updated})


@_register_handler("tq_start")
def tq_start(task_id: str) -> str:
    """Mark an assigned task as running."""
    task = _find_task(task_id)
    if task is None:
        return json.dumps({"error": f"Task '{task_id}' not found"})
    if task["status"] != "assigned":
        return json.dumps(
            {
                "error": f"Cannot start task in '{task['status']}' status. Must be 'assigned'"
            }
        )

    updated = _update_task(task_id, {"status": "running"})
    return json.dumps({"success": True, "task": updated})


@_register_handler("tq_complete")
def tq_complete(task_id: str, result_path: str) -> str:
    """Mark a running task as completed."""
    task = _find_task(task_id)
    if task is None:
        return json.dumps({"error": f"Task '{task_id}' not found"})
    if task["status"] != "running":
        return json.dumps(
            {
                "error": f"Cannot complete task in '{task['status']}' status. Must be 'running'"
            }
        )

    updated = _update_task(
        task_id,
        {
            "status": "completed",
            "result_path": result_path,
        },
    )
    return json.dumps({"success": True, "task": updated})


@_register_handler("tq_fail")
def tq_fail(task_id: str, error: str) -> str:
    """Mark a running task as failed."""
    task = _find_task(task_id)
    if task is None:
        return json.dumps({"error": f"Task '{task_id}' not found"})
    if task["status"] != "running":
        return json.dumps(
            {
                "error": f"Cannot fail task in '{task['status']}' status. Must be 'running'"
            }
        )

    updated = _update_task(
        task_id,
        {
            "status": "failed",
            "error": error,
        },
    )
    return json.dumps({"success": True, "task": updated})


@_register_handler("tq_list")
def tq_list(status: str | None = None) -> str:
    """List tasks, optionally filtered by status."""
    data = _load_queue()
    tasks = data.get("tasks", [])
    if status:
        tasks = [t for t in tasks if t["status"] == status]
    return json.dumps({"tasks": tasks, "count": len(tasks)})


@_register_handler("tq_get")
def tq_get(task_id: str) -> str:
    """Get a specific task by ID."""
    task = _find_task(task_id)
    if task is None:
        return json.dumps({"error": f"Task '{task_id}' not found"})
    return json.dumps({"task": task})


@_register_handler("tq_stats")
def tq_stats() -> str:
    """Get task queue statistics."""
    data = _load_queue()
    tasks = data.get("tasks", [])
    total = len(tasks)
    completed = sum(1 for t in tasks if t["status"] == "completed")
    failed = sum(1 for t in tasks if t["status"] == "failed")
    pending = sum(1 for t in tasks if t["status"] == "pending")
    assigned = sum(1 for t in tasks if t["status"] == "assigned")
    running = sum(1 for t in tasks if t["status"] == "running")
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


@_register_handler("tq_set_mode")
def tq_set_mode(mode: str) -> str:
    """Set the manager mode."""
    try:
        ManagerMode(mode)
    except ValueError:
        return json.dumps(
            {
                "error": f"Invalid mode '{mode}'. Must be one of: idle, dispatching, monitoring"
            }
        )

    data = _load_queue()
    data["mode"] = mode
    _save_queue(data)
    return json.dumps({"success": True, "mode": mode})


@_register_handler("tq_get_mode")
def tq_get_mode() -> str:
    """Get the current manager mode."""
    data = _load_queue()
    return json.dumps({"mode": data.get("mode", ManagerMode.IDLE.value)})


# ============================================================================
# MCP Protocol
# ============================================================================


def handle_tool_call(tool_name: str, args: dict) -> str:
    """Dispatch tool call to handler, returns JSON result string."""
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    try:
        return handler(**args)
    except TypeError as e:
        return json.dumps({"error": f"Invalid arguments for '{tool_name}': {e}"})


def write_json(obj: dict) -> None:
    """Write JSON response to stdout."""
    print(json.dumps(obj), flush=True)


def main() -> None:
    """Main MCP server loop."""
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

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
                            "serverInfo": {"name": "task-queue", "version": "1.0.0"},
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
