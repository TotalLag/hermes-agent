#!/usr/bin/env python3
import json
import asyncio
from typing import Optional

STATE_DIR = "~/.hermes/hiclaw"

_worker_registry = None
_manager_state = None
_lifecycle_logger = None


def _get_registry():
    global _worker_registry
    if _worker_registry is None:
        from gateway.hiclaw.worker_registry import WorkerRegistry

        _worker_registry = WorkerRegistry(STATE_DIR)
    return _worker_registry


def _get_manager_state():
    global _manager_state
    if _manager_state is None:
        from gateway.hiclaw.manager_state import ManagerState

        _manager_state = ManagerState(STATE_DIR)
    return _manager_state


def _get_lifecycle_logger():
    global _lifecycle_logger
    if _lifecycle_logger is None:
        from gateway.hiclaw.lifecycle_logger import LifecycleLogger

        _lifecycle_logger = LifecycleLogger(STATE_DIR)
    return _lifecycle_logger


def hiclaw_list_workers(status: Optional[str] = None) -> str:
    registry = _get_registry()
    workers = (
        asyncio.get_event_loop().run_until_complete(registry.list_workers(status))
        if status
        else asyncio.get_event_loop().run_until_complete(registry.list_workers())
    )
    if not workers:
        return json.dumps({"workers": [], "count": 0})
    return json.dumps(
        {
            "workers": [
                {
                    "id": w.id,
                    "name": w.name,
                    "status": w.status,
                    "capabilities": w.capabilities,
                    "last_seen": w.last_seen_at,
                }
                for w in workers
            ],
            "count": len(workers),
        }
    )


def hiclaw_get_worker(worker_id: str) -> str:
    registry = _get_registry()
    worker = asyncio.get_event_loop().run_until_complete(registry.get_worker(worker_id))
    if not worker:
        return json.dumps({"error": f"Worker {worker_id} not found"})
    logger = _get_lifecycle_logger()
    history = logger.get_worker_history(worker_id)
    return json.dumps(
        {
            "worker": {
                "id": worker.id,
                "name": worker.name,
                "status": worker.status,
                "capabilities": worker.capabilities,
                "version": worker.version,
                "matrix_user_id": worker.matrix_user_id,
                "registered_at": worker.registered_at,
                "last_seen_at": worker.last_seen_at,
                "room_id": worker.room_id,
            },
            "lifecycle_events": history[-10:] if history else [],
        }
    )


def hiclaw_get_manager_state() -> str:
    state = _get_manager_state()
    mode = asyncio.get_event_loop().run_until_complete(state.get_mode())
    stats = asyncio.get_event_loop().run_until_complete(state.get_stats())
    return json.dumps(
        {
            "mode": mode.value,
            "stats": stats,
        }
    )


def hiclaw_pull_task_specs() -> str:
    import subprocess
    import os

    script_path = os.path.join(
        os.path.dirname(__file__), "..", "hiclaw", "scripts", "hiclaw-sync.sh"
    )
    script_path = os.path.abspath(script_path)

    env = os.environ.copy()
    result = subprocess.run(
        ["bash", script_path, "pull-specs"],
        capture_output=True,
        text=True,
        env=env,
    )
    return json.dumps(
        {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "error": result.stderr.strip() if result.stderr else None,
        }
    )


def hiclaw_push_task_results() -> str:
    import subprocess
    import os

    script_path = os.path.join(
        os.path.dirname(__file__), "..", "hiclaw", "scripts", "hiclaw-sync.sh"
    )
    script_path = os.path.abspath(script_path)

    env = os.environ.copy()
    result = subprocess.run(
        ["bash", script_path, "push-results"],
        capture_output=True,
        text=True,
        env=env,
    )
    return json.dumps(
        {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "error": result.stderr.strip() if result.stderr else None,
        }
    )


def hiclaw_sync_status() -> str:
    import subprocess
    import os

    script_path = os.path.join(
        os.path.dirname(__file__), "..", "hiclaw", "scripts", "hiclaw-sync.sh"
    )
    script_path = os.path.abspath(script_path)

    env = os.environ.copy()
    result = subprocess.run(
        ["bash", script_path, "status"],
        capture_output=True,
        text=True,
        env=env,
    )
    return json.dumps(
        {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "error": result.stderr.strip() if result.stderr else None,
        }
    )


def hiclaw_list_tasks(status: Optional[str] = None) -> str:
    state = _get_manager_state()
    tasks = (
        asyncio.get_event_loop().run_until_complete(state.list_tasks(status))
        if status
        else asyncio.get_event_loop().run_until_complete(state.list_tasks())
    )
    if not tasks:
        return json.dumps({"tasks": [], "count": 0})
    return json.dumps(
        {
            "tasks": [
                {
                    "id": t.id,
                    "status": t.status,
                    "assigned_worker": t.assigned_worker,
                    "created_at": t.created_at,
                    "result_path": t.result_path,
                }
                for t in tasks
            ],
            "count": len(tasks),
        }
    )


def check_hiclaw_requirements() -> bool:
    return True


HICLAW_MANAGER_SCHEMA = {
    "name": "hiclaw_manager",
    "description": (
        "Manage hiclaw workers and tasks. Use when you need to:\n"
        "- List registered workers (hiclaw_list_workers)\n"
        "- Get details about a specific worker (hiclaw_get_worker)\n"
        "- View manager state and statistics (hiclaw_get_manager_state)\n"
        "- List tasks and their status (hiclaw_list_tasks)\n\n"
        "Workers register via Matrix messages. The Manager tracks their\n"
        "status, capabilities, and lifecycle events automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


LIST_WORKERS_SCHEMA = {
    "name": "hiclaw_list_workers",
    "description": (
        "List all registered hiclaw workers. Optionally filter by status.\n"
        "Status values: registered, ready, busy, done, error, offline"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "Filter by worker status",
                "enum": ["registered", "ready", "busy", "done", "error", "offline"],
            },
        },
    },
}


GET_WORKER_SCHEMA = {
    "name": "hiclaw_get_worker",
    "description": "Get detailed information about a specific worker including lifecycle history.",
    "parameters": {
        "type": "object",
        "properties": {
            "worker_id": {
                "type": "string",
                "description": "Worker ID to look up",
            },
        },
        "required": ["worker_id"],
    },
}


GET_MANAGER_STATE_SCHEMA = {
    "name": "hiclaw_get_manager_state",
    "description": "Get current Manager state including mode and task statistics.",
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


LIST_TASKS_SCHEMA = {
    "name": "hiclaw_list_tasks",
    "description": (
        "List all tasks tracked by the Manager. Optionally filter by status.\n"
        "Status values: pending, assigned, running, completed, failed"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "Filter by task status",
                "enum": ["pending", "assigned", "running", "completed", "failed"],
            },
        },
    },
}


PULL_TASK_SPECS_SCHEMA = {
    "name": "hiclaw_pull_task_specs",
    "description": "Pull task specifications from MinIO to local storage (~/.hermes/hiclaw/task-specs/).",
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


PUSH_TASK_RESULTS_SCHEMA = {
    "name": "hiclaw_push_task_results",
    "description": "Push task results from local storage (~/.hermes/hiclaw/task-results/) to MinIO.",
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


SYNC_STATUS_SCHEMA = {
    "name": "hiclaw_sync_status",
    "description": "Get the current sync status between local storage and MinIO for task specs and results.",
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


from tools.registry import registry

registry.register(
    name="hiclaw_manager",
    toolset="hiclaw",
    schema=HICLAW_MANAGER_SCHEMA,
    handler=lambda args, **kw: hiclaw_get_manager_state(),
    check_fn=check_hiclaw_requirements,
    emoji="🤖",
)

registry.register(
    name="hiclaw_list_workers",
    toolset="hiclaw",
    schema=LIST_WORKERS_SCHEMA,
    handler=lambda args, **kw: hiclaw_list_workers(status=args.get("status")),
    check_fn=check_hiclaw_requirements,
    emoji="👥",
)

registry.register(
    name="hiclaw_get_worker",
    toolset="hiclaw",
    schema=GET_WORKER_SCHEMA,
    handler=lambda args, **kw: hiclaw_get_worker(worker_id=args.get("worker_id")),
    check_fn=check_hiclaw_requirements,
    emoji="🔍",
)

registry.register(
    name="hiclaw_get_manager_state",
    toolset="hiclaw",
    schema=GET_MANAGER_STATE_SCHEMA,
    handler=lambda args, **kw: hiclaw_get_manager_state(),
    check_fn=check_hiclaw_requirements,
    emoji="📊",
)

registry.register(
    name="hiclaw_list_tasks",
    toolset="hiclaw",
    schema=LIST_TASKS_SCHEMA,
    handler=lambda args, **kw: hiclaw_list_tasks(status=args.get("status")),
    check_fn=check_hiclaw_requirements,
    emoji="📋",
)

registry.register(
    name="hiclaw_pull_task_specs",
    toolset="hiclaw",
    schema=PULL_TASK_SPECS_SCHEMA,
    handler=lambda args, **kw: hiclaw_pull_task_specs(),
    check_fn=check_hiclaw_requirements,
    emoji="📥",
)

registry.register(
    name="hiclaw_push_task_results",
    toolset="hiclaw",
    schema=PUSH_TASK_RESULTS_SCHEMA,
    handler=lambda args, **kw: hiclaw_push_task_results(),
    check_fn=check_hiclaw_requirements,
    emoji="📤",
)

registry.register(
    name="hiclaw_sync_status",
    toolset="hiclaw",
    schema=SYNC_STATUS_SCHEMA,
    handler=lambda args, **kw: hiclaw_sync_status(),
    check_fn=check_hiclaw_requirements,
    emoji="🔄",
)
