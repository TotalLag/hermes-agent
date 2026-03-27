from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from aiohttp import web

try:
    from prometheus_client import (
        Gauge,
        Counter,
        Histogram,
        generate_latest,
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        REGISTRY,
    )

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    Gauge = None
    Counter = None
    Histogram = None
    generate_latest = None
    CONTENT_TYPE_LATEST = None
    CollectorRegistry = None
    REGISTRY = None


def _metrics_enabled() -> bool:
    if not PROMETHEUS_AVAILABLE:
        return False
    return os.getenv("HICLAW_METRICS_ENABLED", "true").lower() in ("1", "true", "yes")


class HiClawMetrics:
    _instance: Optional["HiClawMetrics"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "HiClawMetrics":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        self.enabled = _metrics_enabled()

        if not self.enabled:
            logger.info("HiClaw metrics disabled")
            return

        self.workers_total = Gauge("hiclaw_workers_total", "Total registered workers")
        self.workers_ready = Gauge("hiclaw_workers_ready", "Workers in ready state")
        self.tasks_pending = Gauge("hiclaw_tasks_pending", "Tasks awaiting assignment")
        self.tasks_completed_total = Counter(
            "hiclaw_tasks_completed_total", "Completed tasks"
        )
        self.tasks_failed_total = Counter("hiclaw_tasks_failed_total", "Failed tasks")
        self.heartbeat_latency_seconds = Histogram(
            "hiclaw_heartbeat_latency_seconds",
            "Heartbeat round-trip time",
            buckets=(
                0.01,
                0.025,
                0.05,
                0.075,
                0.1,
                0.25,
                0.5,
                0.75,
                1.0,
                2.5,
                5.0,
                7.5,
                10.0,
            ),
        )
        logger.info("HiClaw metrics initialized")

    def record_worker_created(self) -> None:
        if self.enabled:
            self.workers_total.inc()

    def record_worker_destroyed(self) -> None:
        if self.enabled:
            self.workers_total.dec()

    def record_worker_ready(self) -> None:
        if self.enabled:
            self.workers_ready.inc()

    def record_worker_not_ready(self) -> None:
        if self.enabled:
            self.workers_ready.dec()

    def record_task_pending(self) -> None:
        if self.enabled:
            self.tasks_pending.inc()

    def record_task_assigned(self) -> None:
        if self.enabled:
            self.tasks_pending.dec()

    def record_task_completed(self) -> None:
        if self.enabled:
            self.tasks_completed_total.inc()

    def record_task_failed(self) -> None:
        if self.enabled:
            self.tasks_failed_total.inc()

    def record_heartbeat_latency(self, latency_seconds: float) -> None:
        if self.enabled:
            self.heartbeat_latency_seconds.observe(latency_seconds)

    def refresh_from_db(self) -> None:
        if not self.enabled:
            return

        try:
            registry_db = os.path.expanduser("~/.hermes/hiclaw/workers-registry.db")
            task_db = os.path.expanduser("~/.hermes/hiclaw/task-queue.db")

            if os.path.exists(registry_db):
                conn = sqlite3.connect(registry_db)
                conn.row_factory = sqlite3.Row
                total = conn.execute("SELECT COUNT(*) FROM workers").fetchone()[0]
                ready = conn.execute(
                    "SELECT COUNT(*) FROM workers WHERE status='ready'"
                ).fetchone()[0]
                self.workers_total.set(total)
                self.workers_ready.set(ready)
                conn.close()

            if os.path.exists(task_db):
                conn = sqlite3.connect(task_db)
                conn.row_factory = sqlite3.Row
                pending = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status='pending'"
                ).fetchone()[0]
                self.tasks_pending.set(pending)
                conn.close()

        except Exception as e:
            logger.warning("Failed to refresh metrics from DB: %s", e)


metrics = HiClawMetrics()


if PROMETHEUS_AVAILABLE:

    class MetricsServer:
        def __init__(
            self,
            host: str = "0.0.0.0",
            port: int = 9090,
            registry: CollectorRegistry = REGISTRY,
        ) -> None:
            self.host = host
            self.port = port
            self.registry = registry
            self._server: Optional[asyncio.Server] = None
            self._shutdown_event = asyncio.Event()

        async def start(self) -> None:
            if self._server is not None:
                logger.warning("MetricsServer already running")
                return

            from aiohttp import web

            async def handle_metrics(request: web.Request) -> web.Response:
                metrics.refresh_from_db()
                try:
                    data = generate_latest(self.registry)
                except Exception as e:
                    logger.error("Error generating metrics: %s", e)
                    return web.Response(status=500, text="Error generating metrics")
                return web.Response(body=data, content_type=CONTENT_TYPE_LATEST)

            self._server = await asyncio.start_server(
                handle_metrics, self.host, self.port
            )
            addr = self._server.sockets[0].getsockname()
            logger.info("HiClaw metrics server listening on %s:%s", addr[0], addr[1])

        async def stop(self) -> None:
            if self._server is None:
                return
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("HiClaw metrics server stopped")

        @property
        def is_running(self) -> bool:
            return self._server is not None

else:

    class MetricsServer:
        def __init__(
            self,
            host: str = "0.0.0.0",
            port: int = 9090,
            registry=None,
        ) -> None:
            self.host = host
            self.port = port
            self.enabled = False
            logger.warning(
                "prometheus_client not available; MetricsServer will not function"
            )

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        @property
        def is_running(self) -> bool:
            return False
