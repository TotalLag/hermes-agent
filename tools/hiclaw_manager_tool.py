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


def hiclaw_create_worker(
    worker_name: str,
    image: str = "localhost/hermes-worker:latest",
    runtime: str = "hermes",
) -> str:
    import os
    import uuid
    import requests

    docker_proxy = os.environ.get(
        "HICLAW_DOCKER_PROXY_HOST", "http://hiclaw-docker-proxy:2375"
    )
    matrix_domain = os.environ.get("HICLAW_MATRIX_DOMAIN", "")
    matrix_internal = os.environ.get(
        "HICLAW_MATRIX_INTERNAL_URL", "http://hiclaw-manager:6167"
    )
    manager_room_id = os.environ.get("HICLAW_MANAGER_ROOM_ID", "")
    mc_host = os.environ.get("HICLAW_MC_HOST", "")
    minio_host = os.environ.get("HICLAW_MINIO_HOST", "http://hiclaw-manager:9000")
    bucket = os.environ.get("HICLAW_BUCKET", "hiclaw-storage")
    access_key = os.environ.get("HICLAW_ACCESS_KEY", "")
    secret_key = os.environ.get("HICLAW_SECRET_KEY", "")
    registration_token = os.environ.get("HICLAW_REGISTRATION_TOKEN", "")
    storage_bucket = os.environ.get("HICLAW_STORAGE_BUCKET", bucket)
    storage_prefix = os.environ.get("HICLAW_STORAGE_PREFIX", "agents")
    task_specs_prefix = os.environ.get("HICLAW_TASK_SPECS_PREFIX", "task-specs/")
    task_results_prefix = os.environ.get("HICLAW_TASK_RESULTS_PREFIX", "task-results/")

    errors = []
    if not manager_room_id:
        errors.append("HICLAW_MANAGER_ROOM_ID is not set")
    if not docker_proxy:
        errors.append("HICLAW_DOCKER_PROXY_HOST is not set")

    if errors:
        return json.dumps({"success": False, "error": "; ".join(errors)})

    worker_id = f"{worker_name}-{uuid.uuid4().hex[:8]}"
    matrix_user = f"@{worker_id}:{matrix_domain}" if matrix_domain else f"@{worker_id}"

    matrix_password = uuid.uuid4().hex + uuid.uuid4().hex

    if matrix_internal and registration_token:
        try:
            reg_resp = requests.post(
                f"{matrix_internal}/_matrix/client/v3/register",
                json={
                    "auth": {
                        "type": "m.login.registration_token",
                        "token": registration_token,
                    },
                    "username": worker_id,
                    "password": matrix_password,
                    "device_id": f"HERMES-{worker_id[:8]}",
                    "initial_device_display_name": f"hermes-worker/{worker_name}",
                },
                timeout=30,
            )
            if reg_resp.status_code not in (200, 201):
                reg_data = reg_resp.json() if reg_resp.content else {}
                if reg_resp.status_code == 401:
                    flows = reg_data.get("flows", [])
                    for flow in flows:
                        if all(
                            s.get("stage") == "m.login.registration_token"
                            for s in flow.get("stages", [])
                        ):
                            reg_resp = requests.post(
                                f"{matrix_internal}/_matrix/client/v3/register",
                                json={
                                    "auth": {
                                        "type": "m.login.registration_token",
                                        "token": registration_token,
                                    },
                                    "username": worker_id,
                                    "password": matrix_password,
                                },
                                timeout=30,
                            )
                            break
                if reg_resp.status_code not in (200, 201):
                    return json.dumps(
                        {
                            "success": False,
                            "error": f"Matrix registration failed: {reg_resp.status_code} {reg_resp.text[:200]}",
                        }
                    )
            reg_data = reg_resp.json()
            worker_token = reg_data.get("access_token", "")
            worker_device_id = reg_data.get("device_id", f"HERMES-{worker_id[:8]}")
        except Exception as e:
            return json.dumps(
                {"success": False, "error": f"Matrix registration error: {str(e)}"}
            )
    elif matrix_domain:
        try:
            reg_resp = requests.post(
                f"https://{matrix_domain}/_matrix/client/r0/register",
                json={
                    "auth": {"type": "m.login.dummy"},
                    "username": worker_id,
                    "password": matrix_password,
                    "device_id": f"HERMES-{worker_id[:8]}",
                    "initial_device_display_name": f"hermes-worker/{worker_name}",
                },
                timeout=30,
            )
            if reg_resp.status_code not in (200, 201):
                reg_data = reg_resp.json() if reg_resp.content else {}
                if reg_resp.status_code == 401:
                    flows = reg_data.get("flows", [])
                    for flow in flows:
                        if all(
                            s.get("stage") == "m.login.dummy"
                            for s in flow.get("stages", [])
                        ):
                            reg_resp = requests.post(
                                f"https://{matrix_domain}/_matrix/client/r0/register",
                                json={
                                    "auth": {"type": "m.login.dummy"},
                                    "username": worker_id,
                                    "password": matrix_password,
                                },
                                timeout=30,
                            )
                            break
                if reg_resp.status_code not in (200, 201):
                    return json.dumps(
                        {
                            "success": False,
                            "error": f"Matrix registration failed: {reg_resp.status_code} {reg_resp.text[:200]}",
                        }
                    )
            reg_data = reg_resp.json()
            worker_token = reg_data.get("access_token", "")
            worker_device_id = reg_data.get("device_id", f"HERMES-{worker_id[:8]}")
        except Exception as e:
            return json.dumps(
                {"success": False, "error": f"Matrix registration error: {str(e)}"}
            )
    else:
        worker_token = os.environ.get("HICLAW_MATRIX_ACCESS_TOKEN", "")
        worker_device_id = f"HERMES-{worker_id[:8]}"

    container_env = [
        f"HICLAW_WORKER_NAME={worker_name}",
        f"HICLAW_MATRIX_HOMESERVER=https://{matrix_domain}",
        f"HICLAW_MATRIX_INTERNAL_URL={matrix_internal if matrix_internal else f'http://hiclaw-manager:6167'}",
        f"HICLAW_MATRIX_USER_ID={matrix_user}",
        f"HICLAW_MATRIX_ACCESS_TOKEN={worker_token}",
        f"HICLAW_MATRIX_DEVICE_ID={worker_device_id}",
        f"HICLAW_MANAGER_ROOM_ID={manager_room_id}",
        f"HICLAW_MC_HOST={mc_host}",
        f"HICLAW_MINIO_HOST={minio_host}",
        f"HICLAW_BUCKET={storage_bucket}",
        f"HICLAW_ACCESS_KEY={access_key}",
        f"HICLAW_SECRET_KEY={secret_key}",
        f"HICLAW_STORAGE_BUCKET={storage_bucket}",
        f"HICLAW_STORAGE_PREFIX={storage_prefix}",
        f"HICLAW_TASK_SPECS_PREFIX={task_specs_prefix}",
        f"HICLAW_TASK_RESULTS_PREFIX={task_results_prefix}",
        "HICLAW_HERMES_MODE=gateway",
        f"HERMES_WORKER_IMAGE={image}",
    ]

    try:
        create_resp = requests.post(
            f"{docker_proxy}/containers/create?name={worker_id}",
            json={
                "Image": image,
                "Env": container_env,
                "HostConfig": {
                    "NetworkMode": "hiclaw-net",
                    "ExtraHosts": ["host.docker.internal:host-gateway"],
                },
                "Labels": {
                    "hermes.worker": "true",
                    "hermes.worker.name": worker_name,
                    "hermes.worker.runtime": runtime,
                },
            },
            timeout=30,
        )
        if create_resp.status_code not in (200, 201):
            return json.dumps(
                {
                    "success": False,
                    "error": f"Container create failed: {create_resp.status_code} {create_resp.text[:200]}",
                }
            )
        container_data = create_resp.json()
        container_id = container_data.get("Id", "")[:12]
    except Exception as e:
        return json.dumps(
            {"success": False, "error": f"Container create error: {str(e)}"}
        )

    try:
        start_resp = requests.post(
            f"{docker_proxy}/containers/{container_id}/start",
            timeout=30,
        )
        if start_resp.status_code not in (200, 204):
            return json.dumps(
                {
                    "success": False,
                    "error": f"Container start failed: {start_resp.status_code} {start_resp.text[:200]}",
                    "container_id": container_id,
                }
            )
    except Exception as e:
        return json.dumps(
            {
                "success": False,
                "error": f"Container start error: {str(e)}",
                "container_id": container_id,
            }
        )

    return json.dumps(
        {
            "success": True,
            "worker_id": worker_id,
            "container_id": container_id,
            "matrix_user_id": matrix_user,
            "runtime": runtime,
            "image": image,
            "message": f"Worker '{worker_name}' ({worker_id}) created and started",
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


CREATE_WORKER_SCHEMA = {
    "name": "hiclaw_create_worker",
    "description": (
        "Spawn a new hermes worker container via the Docker proxy API.\n"
        "Registers a Matrix account for the worker, creates the container with all required\n"
        "hiclaw environment variables, and starts it. The worker self-registers with the\n"
        "hiclaw Manager via Matrix message once it starts.\n\n"
        "Requires: HICLAW_DOCKER_PROXY_HOST (default: http://hiclaw-docker-proxy:2375),\n"
        "HICLAW_MANAGER_ROOM_ID, HICLAW_MATRIX_DOMAIN.\n\n"
        "Default image: ghcr.io/totallag/hermes-worker:latest\n"
        "Default runtime: hermes"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "worker_name": {
                "type": "string",
                "description": "Human-readable worker name (used as hostname prefix)",
            },
            "image": {
                "type": "string",
                "description": "Docker image to use for the worker (default: ghcr.io/totallag/hermes-worker:latest)",
            },
            "runtime": {
                "type": "string",
                "description": "Worker runtime type (default: hermes)",
            },
        },
        "required": ["worker_name"],
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

registry.register(
    name="hiclaw_create_worker",
    toolset="hiclaw",
    schema=CREATE_WORKER_SCHEMA,
    handler=lambda args, **kw: hiclaw_create_worker(
        worker_name=args.get("worker_name", ""),
        image=args.get("image", "ghcr.io/totallag/hermes-worker:latest"),
        runtime=args.get("runtime", "hermes"),
    ),
    check_fn=check_hiclaw_requirements,
    emoji="🚀",
)
