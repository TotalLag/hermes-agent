from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

REGISTRY_PATH = os.path.expanduser("~/.hermes/hiclaw/workers-registry.json")
VALID_STATUSES = {"registered", "ready", "busy", "done", "error", "offline"}


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
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)


def _validate_capabilities(capabilities: object) -> list[str]:
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) for item in capabilities
    ):
        raise ValueError("capabilities must be a list of strings")
    return capabilities


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


def _load_registry() -> list[dict]:
    if not os.path.exists(REGISTRY_PATH):
        return []

    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as exc:
        logger.warning("Invalid registry JSON at %s: %s", REGISTRY_PATH, exc)
        return []
    except OSError as exc:
        logger.warning("Failed reading registry %s: %s", REGISTRY_PATH, exc)
        return []

    if not isinstance(data, list):
        logger.warning(
            "Registry file %s does not contain a list; ignoring", REGISTRY_PATH
        )
        return []

    workers: list[dict] = []
    for item in data:
        try:
            workers.append(asdict(_worker_from_dict(item)))
        except ValueError as exc:
            logger.warning("Skipping invalid worker record in registry: %s", exc)
    return workers


def _save_registry(workers: list[dict]) -> None:
    _ensure_registry_dir()
    directory = os.path.dirname(REGISTRY_PATH)

    fd, temp_path = tempfile.mkstemp(
        prefix="workers-registry-", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(workers, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, REGISTRY_PATH)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _find_worker(worker_id: str) -> dict | None:
    for worker in _load_registry():
        if worker.get("worker_id") == worker_id:
            return worker
    return None


def _require_worker_id(worker_id: str) -> None:
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValueError("worker_id is required")


def _validate_status(status: str) -> str:
    if not isinstance(status, str) or not status.strip():
        raise ValueError("status is required")
    normalized = status.strip()
    if normalized not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status '{normalized}'. Valid: {', '.join(sorted(VALID_STATUSES))}"
        )
    return normalized


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

    workers = _load_registry()
    if any(worker.get("worker_id") == worker_id for worker in workers):
        logger.warning("Duplicate worker registration rejected for %s", worker_id)
        return _error(
            f"Worker {worker_id} already registered. Use wr_heartbeat instead.",
            worker_id=worker_id,
        )

    now = _utc_now()
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
        metadata={},
    )
    workers.append(asdict(worker))
    _save_registry(workers)

    return _ok(status="ok", worker=asdict(worker))


def wr_heartbeat(worker_id: str) -> str:
    try:
        _require_worker_id(worker_id)
    except ValueError as exc:
        return _error(str(exc))

    workers = _load_registry()
    for worker in workers:
        if worker.get("worker_id") == worker_id:
            now = _utc_now()
            worker["last_seen_at"] = now
            _save_registry(workers)
            return _ok(
                status="ok",
                worker_id=worker_id,
                last_seen_at=now,
                worker_status=worker.get("status"),
            )

    logger.warning("Heartbeat received for missing worker %s", worker_id)
    return _error(f"Worker {worker_id} not found.", worker_id=worker_id)


def wr_update_status(worker_id: str, status: str, message: str = "") -> str:
    try:
        _require_worker_id(worker_id)
        normalized_status = _validate_status(status)
        if message is not None and not isinstance(message, str):
            raise ValueError("message must be a string")
    except ValueError as exc:
        return _error(str(exc))

    workers = _load_registry()
    for worker in workers:
        if worker.get("worker_id") == worker_id:
            metadata = dict(worker.get("metadata") or {})
            if message:
                metadata["message"] = message
            elif "message" in metadata:
                metadata.pop("message", None)

            now = _utc_now()
            worker["status"] = normalized_status
            worker["metadata"] = metadata
            worker["last_seen_at"] = now
            _save_registry(workers)
            return _ok(
                status="ok",
                worker_id=worker_id,
                worker_status=normalized_status,
                last_seen_at=now,
                metadata=metadata,
            )

    logger.warning("Status update received for missing worker %s", worker_id)
    return _error(f"Worker {worker_id} not found.", worker_id=worker_id)


def wr_remove(worker_id: str) -> str:
    try:
        _require_worker_id(worker_id)
    except ValueError as exc:
        return _error(str(exc))

    workers = _load_registry()
    filtered_workers = [
        worker for worker in workers if worker.get("worker_id") != worker_id
    ]
    if len(filtered_workers) == len(workers):
        logger.warning("Remove requested for missing worker %s", worker_id)
        return _error(f"Worker {worker_id} not found.", worker_id=worker_id)

    _save_registry(filtered_workers)
    return _ok(status="ok", removed=True, worker_id=worker_id)


def wr_list(status: str | None = None) -> str:
    try:
        normalized_status = None
        if status is not None and str(status).strip() != "":
            normalized_status = _validate_status(str(status))
    except ValueError as exc:
        return _error(str(exc))

    workers = _load_registry()
    if normalized_status is not None:
        workers = [
            worker for worker in workers if worker.get("status") == normalized_status
        ]

    return _ok(status="ok", count=len(workers), workers=workers)


def wr_get(worker_id: str) -> str:
    try:
        _require_worker_id(worker_id)
    except ValueError as exc:
        return _error(str(exc))

    worker = _find_worker(worker_id)
    if worker is None:
        logger.warning("Lookup requested for missing worker %s", worker_id)
        return _error(f"Worker {worker_id} not found.", worker_id=worker_id)

    return _ok(status="ok", worker=worker)


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
                "room_id",
            ],
        },
    },
    {
        "name": "wr_heartbeat",
        "description": "Refresh a worker's last_seen_at timestamp.",
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
        "description": "Update a worker's status and optional status message.",
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
]


TOOL_HANDLERS = {
    "wr_register": wr_register,
    "wr_heartbeat": wr_heartbeat,
    "wr_update_status": wr_update_status,
    "wr_remove": wr_remove,
    "wr_list": wr_list,
    "wr_get": wr_get,
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
                                "version": "1.0.0",
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
