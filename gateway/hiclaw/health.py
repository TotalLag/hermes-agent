"""
Kubernetes-style health probes for the hiClaw gateway.

Provides:
- LivenessProbe: Is the gateway process alive?
- ReadinessProbe: Is gateway ready to serve (MCP servers up, Docker up)?
- HealthProbe: Combined status (liveness + readiness)

All probes return JSON {"status": "ok"|"degraded"|"error", ...} and use
max 2 second timeouts to avoid blocking Kubernetes probes.
"""

import json
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import docker
from docker.errors import APIError as DockerAPIError
from docker.errors import NotFound as DockerNotFound

from .circuit_breaker import CircuitState, get_docker_circuit_breaker

logger = logging.getLogger(__name__)

# Timeout for health checks (max 2 seconds per Kubernetes probe requirement)
HEALTH_TIMEOUT_SECONDS = 2.0

# Path to mcp-servers directory
MCP_SERVERS_DIR = Path(__file__).resolve().parents[2] / "mcp-servers"


@dataclass
class ProbeResult:
    """Result of a health probe check."""

    status: str  # "ok", "degraded", or "error"
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON response."""
        result = {"status": self.status}
        if self.message:
            result["message"] = self.message
        if self.details:
            result.update(self.details)
        return result


class LivenessProbe:
    """
    Kubernetes liveness probe - checks if the gateway process is alive.

    Returns {"status": "ok", "pid": <pid>} if the process is alive.
    This does NOT check external dependencies (MCP servers, Docker, etc.).
    """

    def check(self) -> ProbeResult:
        """
        Perform liveness check.

        Returns:
            ProbeResult with status "ok" if process is alive, "error" otherwise.
        """
        try:
            pid = os.getpid()
            # Check if process is responsive by sending signal 0 (doesn't actually send)
            os.kill(pid, 0)
            return ProbeResult(
                status="ok",
                details={"pid": pid},
            )
        except OSError as e:
            return ProbeResult(
                status="error",
                message=f"Process not responsive: {e}",
                details={"pid": os.getpid()},
            )


class ReadinessProbe:
    """
    Kubernetes readiness probe - checks if gateway is ready to serve.

    Checks:
    1. Worker-registry MCP server is reachable
    2. Task-queue MCP server is reachable
    3. Docker daemon is reachable

    Returns {"status": "ok|degraded", "checks": {...}, "circuit_breaker": <state>}.
    Returns HTTP 503 when status != "ok" for K8s probes.
    """

    def __init__(self):
        self._lock = threading.Lock()

    def check(self) -> ProbeResult:
        """
        Perform readiness check across all dependencies.

        Returns:
            ProbeResult with status "ok" if all checks pass,
            "degraded" if some checks fail but gateway can still serve,
            "error" if critical checks fail.
        """
        checks: Dict[str, Any] = {}
        unhealthy: list[str] = []
        degraded: list[str] = []

        # Check worker-registry MCP server
        wr_result = self._check_worker_registry()
        checks["worker_registry"] = wr_result
        if wr_result["status"] != "ok":
            unhealthy.append("worker-registry")

        # Check task-queue MCP server
        tq_result = self._check_task_queue()
        checks["task_queue"] = tq_result
        if tq_result["status"] != "ok":
            unhealthy.append("task-queue")

        # Check Docker daemon
        docker_result = self._check_docker()
        checks["docker"] = docker_result
        if docker_result["status"] != "ok":
            degraded.append("docker")

        # Get circuit breaker state
        circuit_breaker_state = self._get_circuit_breaker_state()
        checks["circuit_breaker"] = circuit_breaker_state

        # Determine overall status
        if unhealthy:
            # Critical components unreachable - gateway cannot serve
            return ProbeResult(
                status="error" if len(unhealthy) >= 2 else "degraded",
                message=f"Unhealthy components: {', '.join(unhealthy)}",
                details={
                    "checks": checks,
                    "unhealthy": unhealthy,
                    "degraded": degraded,
                    "circuit_breaker": circuit_breaker_state,
                },
            )

        if degraded:
            # Docker is degraded but not critical
            return ProbeResult(
                status="degraded",
                message=f"Degraded components: {', '.join(degraded)}",
                details={
                    "checks": checks,
                    "unhealthy": unhealthy,
                    "degraded": degraded,
                    "circuit_breaker": circuit_breaker_state,
                },
            )

        return ProbeResult(
            status="ok",
            details={
                "checks": checks,
                "circuit_breaker": circuit_breaker_state,
            },
        )

    def _check_worker_registry(self) -> Dict[str, Any]:
        """Check if worker-registry MCP server is reachable."""
        try:
            WR_PATH = MCP_SERVERS_DIR / "worker-registry" / "server.py"
            if not WR_PATH.exists():
                return {
                    "status": "error",
                    "error": "worker-registry server.py not found",
                }

            import importlib.util

            spec = importlib.util.spec_from_file_location("wr_health", WR_PATH)
            if spec is None or spec.loader is None:
                return {
                    "status": "error",
                    "error": "Failed to load worker-registry spec",
                }

            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # Call wr_list() to check reachability - use timeout via threading
            result: Dict[str, Any] = {"status": "ok"}
            error_holder: Dict[str, Any] = {"error": None}

            def call_wr_list():
                try:
                    response = mod.wr_list(None)
                    data = json.loads(response)
                    result["workers"] = {
                        "total": len(data.get("workers", [])),
                    }
                except Exception as e:
                    error_holder["error"] = str(e)

            t = threading.Thread(target=call_wr_list)
            t.daemon = True
            t.start()
            t.join(timeout=HEALTH_TIMEOUT_SECONDS)

            if t.is_alive():
                return {
                    "status": "error",
                    "error": "Timeout contacting worker-registry",
                }
            if error_holder["error"]:
                return {"status": "error", "error": error_holder["error"]}

            return result

        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _check_task_queue(self) -> Dict[str, Any]:
        """Check if task-queue MCP server is reachable."""
        try:
            TQ_PATH = MCP_SERVERS_DIR / "task-queue" / "server.py"
            if not TQ_PATH.exists():
                return {"status": "error", "error": "task-queue server.py not found"}

            import importlib.util

            spec = importlib.util.spec_from_file_location("tq_health", TQ_PATH)
            if spec is None or spec.loader is None:
                return {"status": "error", "error": "Failed to load task-queue spec"}

            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # Call tq_stats() to check reachability - use timeout via threading
            result: Dict[str, Any] = {"status": "ok"}
            error_holder: Dict[str, Any] = {"error": None}

            def call_tq_stats():
                try:
                    response = mod.tq_stats()
                    data = json.loads(response)
                    result["tasks"] = {
                        "pending": data.get("pending", 0),
                        "assigned": data.get("assigned", 0),
                        "running": data.get("running", 0),
                    }
                except Exception as e:
                    error_holder["error"] = str(e)

            t = threading.Thread(target=call_tq_stats)
            t.daemon = True
            t.start()
            t.join(timeout=HEALTH_TIMEOUT_SECONDS)

            if t.is_alive():
                return {"status": "error", "error": "Timeout contacting task-queue"}
            if error_holder["error"]:
                return {"status": "error", "error": error_holder["error"]}

            return result

        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _check_docker(self) -> Dict[str, Any]:
        """Check if Docker daemon is reachable."""
        try:
            # Check circuit breaker first
            cb = get_docker_circuit_breaker()
            if cb.state == CircuitState.OPEN:
                return {
                    "status": "degraded",
                    "error": "Docker circuit breaker is open",
                    "circuit_state": cb.state.value,
                }

            # Try to ping Docker
            client = docker.from_env()
            client.ping()
            return {"status": "ok"}

        except DockerNotFound as e:
            return {"status": "error", "error": f"Docker not found: {e}"}
        except DockerAPIError as e:
            return {"status": "error", "error": f"Docker API error: {e}"}
        except Exception as e:
            return {"status": "degraded", "error": str(e)}

    def _get_circuit_breaker_state(self) -> Dict[str, Any]:
        """Get current Docker circuit breaker state."""
        try:
            cb = get_docker_circuit_breaker()
            return cb.get_state()
        except Exception as e:
            return {"error": str(e)}


class HealthProbe:
    """
    Combined health probe - checks both liveness and readiness.

    Returns {"status": "ok"|"degraded"|"error", "liveness": {...}, "readiness": {...}}.
    This is the backwards-compatible /health endpoint.
    """

    def __init__(self):
        self._liveness = LivenessProbe()
        self._readiness = ReadinessProbe()

    def check(self) -> ProbeResult:
        """
        Perform combined health check.

        Returns:
            ProbeResult with overall status combining liveness and readiness.
        """
        liveness_result = self._liveness.check()
        readiness_result = self._readiness.check()

        # Determine overall status
        if liveness_result.status == "error":
            overall_status = "error"
        elif readiness_result.status == "error":
            overall_status = "degraded"
        elif readiness_result.status == "degraded":
            overall_status = "degraded"
        else:
            overall_status = "ok"

        return ProbeResult(
            status=overall_status,
            details={
                "liveness": liveness_result.to_dict(),
                "readiness": readiness_result.to_dict(),
            },
        )


# Global probe instances for reuse
_liveness_probe: Optional[LivenessProbe] = None
_readiness_probe: Optional[ReadinessProbe] = None
_health_probe: Optional[HealthProbe] = None
_probe_lock = threading.Lock()


def get_liveness_probe() -> LivenessProbe:
    """Get or create global LivenessProbe instance."""
    global _liveness_probe
    with _probe_lock:
        if _liveness_probe is None:
            _liveness_probe = LivenessProbe()
        return _liveness_probe


def get_readiness_probe() -> ReadinessProbe:
    """Get or create global ReadinessProbe instance."""
    global _readiness_probe
    with _probe_lock:
        if _readiness_probe is None:
            _readiness_probe = ReadinessProbe()
        return _readiness_probe


def get_health_probe() -> HealthProbe:
    """Get or create global HealthProbe instance."""
    global _health_probe
    with _probe_lock:
        if _health_probe is None:
            _health_probe = HealthProbe()
        return _health_probe
