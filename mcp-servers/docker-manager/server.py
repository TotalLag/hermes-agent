#!/usr/bin/env python3
"""
Docker Manager MCP Server

Manages worker container lifecycle via the docker Python SDK.
Workers are Docker containers that register themselves with the manager via Matrix.

Architecture: Hermes MCP servers are subprocess stdio-based — communicate via JSON-RPC over stdin/stdout.
Pattern: TOOL_DEFINITIONS list + TOOL_HANDLERS dict + handle_tool_call(tool_name, args) main loop
"""

import docker
from docker.errors import DockerException, NotFound
import json
import logging
import os
import sys
import signal
import threading
import time
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, Optional, Tuple

_client: Optional[docker.DockerClient] = None


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    def __init__(
        self, message: str = "Circuit breaker open, Docker operations suspended"
    ):
        self.message = message
        super().__init__(self.message)


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        failure_window: float = 300.0,
        cooldown_timeout: float = 300.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.failure_window = failure_window
        self.cooldown_timeout = cooldown_timeout
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._failure_timestamps: list[float] = []
        self._last_failure_time: Optional[float] = None
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._check_transitions()
            return self._state

    def _check_transitions(self) -> None:
        if self._state == CircuitState.OPEN:
            if self._last_failure_time is not None:
                if time.time() - self._last_failure_time >= self.cooldown_timeout:
                    logging.warning(
                        "Circuit breaker '%s' transitioning OPEN -> HALF_OPEN",
                        self.name,
                    )
                    self._state = CircuitState.HALF_OPEN
                    self._failure_count = 0
                    self._failure_timestamps = []

    def _record_failure(self) -> None:
        now = time.time()
        self._failure_timestamps.append(now)
        self._last_failure_time = now
        cutoff = now - self.failure_window
        self._failure_timestamps = [t for t in self._failure_timestamps if t > cutoff]
        self._failure_count = len(self._failure_timestamps)
        if self._failure_count >= self.failure_threshold:
            if self._state == CircuitState.CLOSED:
                logging.warning(
                    "Circuit breaker '%s' transitioning CLOSED -> OPEN "
                    "(%d failures in %.0f seconds)",
                    self.name,
                    self._failure_count,
                    self.failure_window,
                )
                self._state = CircuitState.OPEN

    def _record_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            logging.info(
                "Circuit breaker '%s' transitioning HALF_OPEN -> CLOSED, Docker operations resumed",
                self.name,
            )
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._failure_timestamps = []
            self._last_failure_time = None

    def call(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        with self._lock:
            self._check_transitions()
            if self._state == CircuitState.OPEN:
                logging.error(
                    "Circuit breaker '%s' is OPEN - rejecting call to %s",
                    self.name,
                    func.__name__,
                )
                raise CircuitOpenError()
        try:
            result = func(*args, **kwargs)
            with self._lock:
                self._record_success()
            return result
        except Exception as e:
            with self._lock:
                self._record_failure()
                if self._state == CircuitState.OPEN:
                    logging.warning(
                        "Circuit breaker '%s' is now OPEN after failure", self.name
                    )
            raise


_docker_circuit_breaker: Optional[CircuitBreaker] = None


def get_docker_circuit_breaker() -> CircuitBreaker:
    global _docker_circuit_breaker
    if _docker_circuit_breaker is None:
        _docker_circuit_breaker = CircuitBreaker(
            name="docker_manager",
            failure_threshold=3,
            failure_window=300.0,
            cooldown_timeout=300.0,
        )
    return _docker_circuit_breaker


DEFAULT_WORKER_IMAGE = os.environ.get("HICLAW_WORKER_IMAGE", "hermes-worker:latest")
MANAGER_CONTAINER_NAME = "hermes-manager"
WORKER_NAME_PREFIX = "hermes-worker-"
MANAGER_MATRIX_USER_ID = os.environ.get("MANAGER_MATRIX_USER_ID", "")
MANAGER_ROOM_ID = os.environ.get("MANAGER_ROOM_ID", "")


def _get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        raise RuntimeError("Docker not available")
    return _client


def is_docker_available() -> bool:
    global _client
    if _client is None:
        return False
    try:
        _client.ping()
        return True
    except DockerException:
        return False


def _worker_name(name: str) -> str:
    return f"{WORKER_NAME_PREFIX}{name}"


def _format_container_info(container: Any) -> Dict[str, Any]:
    return {
        "name": container.name,
        "id": container.id,
        "status": container.status,
        "image": container.image.tags[0]
        if container.image.tags
        else container.image.short_id,
        "created_at": container.attrs.get("Created", ""),
    }


def _init_docker_client() -> None:
    global _client
    try:
        _client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
        _client.ping()
        logging.warning("Docker connection established")
    except Exception as e:
        logging.warning(f"Docker not available: {e}")
        _client = None


def handle_docker_list_containers(arguments: Dict[str, Any]) -> str:
    if not is_docker_available():
        return json.dumps({"error": "Docker is not available"})

    client = _get_client()
    status_filter = arguments.get("status", "all")

    try:
        all_containers = client.containers.list(
            all=True, filters={"name": {"^hermes-worker-": True}}
        )
    except DockerException as e:
        return json.dumps({"error": f"Failed to list containers: {e}"})

    if status_filter == "running":
        containers = [c for c in all_containers if c.status == "running"]
    elif status_filter == "stopped":
        containers = [c for c in all_containers if c.status != "running"]
    else:
        containers = all_containers

    result = [_format_container_info(c) for c in containers]
    return json.dumps({"containers": result})


def handle_docker_create_worker(arguments: Dict[str, Any]) -> str:
    if not is_docker_available():
        return json.dumps({"error": "Docker is not available"})

    name = arguments.get("name", "")
    image = arguments.get("image", DEFAULT_WORKER_IMAGE)
    capabilities = arguments.get("capabilities", [])
    matrix_user_id = arguments.get("matrix_user_id", "")

    if not name:
        return json.dumps({"error": "Worker name is required"})

    full_name = _worker_name(name)
    client = _get_client()

    try:
        existing = client.containers.get(full_name)
        if existing:
            return json.dumps({"error": f"Container '{full_name}' already exists"})
    except NotFound:
        pass
    except DockerException as e:
        return json.dumps({"error": f"Failed to check existing container: {e}"})

    env_vars = [
        f"HERMES_WORKER_ID={name}",
        f"HERMES_WORKER_NAME={name}",
        f"HERMES_WORKER_CAPABILITIES={','.join(capabilities) if capabilities else ''}",
        f"HERMES_WORKER_VERSION=1.0.0",
        f"MATRIX_USER_ID={matrix_user_id}",
        f"MANAGER_MATRIX_USER_ID={MANAGER_MATRIX_USER_ID}",
        f"MANAGER_ROOM_ID={MANAGER_ROOM_ID}",
    ]

    try:
        try:
            client.images.get(image)
        except NotFound:
            logging.warning(f"Image {image} not found, pulling...")
            client.images.pull(image)

        cb = get_docker_circuit_breaker()
        container = cb.call(
            client.containers.run,
            image=image,
            name=full_name,
            detach=True,
            env=env_vars,
            remove=False,
        )

        return json.dumps(
            {
                "id": container.id,
                "name": container.name,
                "status": "created",
            }
        )

    except CircuitOpenError:
        return json.dumps(
            {"error": "Circuit breaker open, Docker operations suspended"}
        )
    except DockerException as e:
        return json.dumps({"error": f"Failed to create container: {e}"})


def handle_docker_remove_worker(arguments: Dict[str, Any]) -> str:
    if not is_docker_available():
        return json.dumps({"error": "Docker is not available"})

    name = arguments.get("name", "")
    if not name:
        return json.dumps({"error": "Worker name is required"})

    full_name = _worker_name(name)
    client = _get_client()

    try:
        cb = get_docker_circuit_breaker()
        container = cb.call(client.containers.get, full_name)

        if container.status == "running":
            container.stop(timeout=10)

        container.remove()

        return json.dumps(
            {
                "name": full_name,
                "status": "removed",
            }
        )

    except CircuitOpenError:
        return json.dumps(
            {"error": "Circuit breaker open, Docker operations suspended"}
        )
    except NotFound:
        return json.dumps({"error": f"Container '{full_name}' not found"})
    except DockerException as e:
        return json.dumps({"error": f"Failed to remove container: {e}"})


def handle_docker_inspect_worker(arguments: Dict[str, Any]) -> str:
    if not is_docker_available():
        return json.dumps({"error": "Docker is not available"})

    name = arguments.get("name", "")
    if not name:
        return json.dumps({"error": "Worker name is required"})

    full_name = _worker_name(name)
    client = _get_client()

    try:
        container = client.containers.get(full_name)
        info = container.attrs

        return json.dumps(
            {
                "id": info.get("Id", ""),
                "name": info.get("Name", "").lstrip("/"),
                "state": info.get("State", {}),
                "config": info.get("Config", {}),
                "created": info.get("Created", ""),
            }
        )

    except NotFound:
        return json.dumps({"error": f"Container '{full_name}' not found"})
    except DockerException as e:
        return json.dumps({"error": f"Failed to inspect container: {e}"})


def handle_docker_get_worker_logs(arguments: Dict[str, Any]) -> str:
    if not is_docker_available():
        return json.dumps({"error": "Docker is not available"})

    name = arguments.get("name", "")
    lines = arguments.get("lines", 100)
    if not name:
        return json.dumps({"error": "Worker name is required"})

    full_name = _worker_name(name)
    client = _get_client()

    try:
        container = client.containers.get(full_name)
        logs = container.logs(tail=lines, timestamps=True).decode("utf-8")

        return json.dumps(
            {
                "name": full_name,
                "lines": lines,
                "logs": logs,
            }
        )

    except NotFound:
        return json.dumps({"error": f"Container '{full_name}' not found"})
    except DockerException as e:
        return json.dumps({"error": f"Failed to get logs: {e}"})


def handle_docker_restart_worker(arguments: Dict[str, Any]) -> str:
    if not is_docker_available():
        return json.dumps({"error": "Docker is not available"})

    name = arguments.get("name", "")
    if not name:
        return json.dumps({"error": "Worker name is required"})

    full_name = _worker_name(name)
    client = _get_client()

    try:
        container = client.containers.get(full_name)

        if container.status != "running":
            return json.dumps(
                {
                    "error": f"Container '{full_name}' is not running (status: {container.status})"
                }
            )

        container.restart(timeout=10)

        return json.dumps(
            {
                "name": full_name,
                "status": "restarted",
            }
        )

    except NotFound:
        return json.dumps({"error": f"Container '{full_name}' not found"})
    except DockerException as e:
        return json.dumps({"error": f"Failed to restart container: {e}"})


def handle_docker_is_available(arguments: Dict[str, Any]) -> str:
    available = is_docker_available()
    status = "available" if available else "unavailable"

    info = {}
    if available:
        try:
            client = _get_client()
            info["version"] = client.version()["Version"]
            info["api_version"] = client.version()["ApiVersion"]
        except Exception:
            pass

    return json.dumps(
        {
            "available": available,
            "status": status,
            "info": info,
        }
    )


TOOL_DEFINITIONS = [
    {
        "name": "docker_list_containers",
        "description": "List all Hermes worker containers. Filter by status: running, stopped, or all (default).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status: 'running', 'stopped', or 'all' (default)",
                    "enum": ["running", "stopped", "all"],
                    "default": "all",
                }
            },
        },
    },
    {
        "name": "docker_create_worker",
        "description": "Create and start a new Hermes worker container.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Unique name for the worker (will be prefixed with hermes-worker-)",
                },
                "image": {
                    "type": "string",
                    "description": "Docker image to use for the worker",
                    "default": "hermes-worker:latest",
                },
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of capabilities this worker supports",
                },
                "matrix_user_id": {
                    "type": "string",
                    "description": "Matrix user ID for this worker to register with",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "docker_remove_worker",
        "description": "Stop and remove a Hermes worker container.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the worker to remove (will be prefixed with hermes-worker-)",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "docker_inspect_worker",
        "description": "Get detailed information about a worker container.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the worker to inspect (will be prefixed with hermes-worker-)",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "docker_get_worker_logs",
        "description": "Get the last N lines of logs from a worker container.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the worker (will be prefixed with hermes-worker-)",
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of log lines to retrieve",
                    "default": 100,
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "docker_restart_worker",
        "description": "Restart a running worker container.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the worker to restart (will be prefixed with hermes-worker-)",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "docker_is_available",
        "description": "Check if the Docker socket is accessible.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

TOOL_HANDLERS = {
    "docker_list_containers": handle_docker_list_containers,
    "docker_create_worker": handle_docker_create_worker,
    "docker_remove_worker": handle_docker_remove_worker,
    "docker_inspect_worker": handle_docker_inspect_worker,
    "docker_get_worker_logs": handle_docker_get_worker_logs,
    "docker_restart_worker": handle_docker_restart_worker,
    "docker_is_available": handle_docker_is_available,
}


def handle_tool_call(tool_name: str, arguments: Dict[str, Any]) -> str:
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    return handler(arguments)


def write_json(obj: Dict[str, Any]) -> None:
    print(json.dumps(obj), flush=True)


def main() -> None:
    try:
        from gateway.hiclaw.logging_config import setup_mcp_logging

        setup_mcp_logging(__name__)
    except ImportError:
        logging.basicConfig(level=logging.WARNING, format="%(message)s")

    _init_docker_client()

    def signal_handler(signum, frame):
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    while True:
        line = sys.stdin.readline()
        if not line:
            break

        try:
            request = json.loads(line)
            method = request.get("method", "")
            req_id = request.get("id")

            if method == "initialize":
                write_json(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "serverInfo": {
                                "name": "docker-manager",
                                "version": "1.0.0",
                            },
                        },
                    }
                )

            elif method == "tools/list":
                write_json(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "tools": TOOL_DEFINITIONS,
                        },
                    }
                )

            elif method == "tools/call":
                tool_name = request["params"]["name"]
                tool_args = request["params"].get("arguments", {})
                result = handle_tool_call(tool_name, tool_args)
                write_json(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": result}],
                        },
                    }
                )

            else:
                write_json(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}",
                        },
                    }
                )

        except json.JSONDecodeError as e:
            write_json(
                {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32700,
                        "message": f"Parse error: {e}",
                    },
                }
            )
        except Exception as e:
            logging.exception("Unhandled error")
            write_json(
                {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32603,
                        "message": str(e),
                    },
                }
            )


if __name__ == "__main__":
    main()
