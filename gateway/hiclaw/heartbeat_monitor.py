"""
HeartbeatMonitor — background task that checks worker health and marks stale workers offline.

Runs as an asyncio Task in the Manager's event loop.
Checks every HICLAW_MANAGER_CHECK_INTERVAL seconds (default 300 = 5 minutes).
"""

import asyncio
import logging
import os
from typing import Optional

from .worker_registry import WorkerRegistry

logger = logging.getLogger(__name__)

CHECK_INTERVAL = int(os.getenv("HICLAW_MANAGER_CHECK_INTERVAL", "300"))


class HeartbeatMonitor:
    """
    Background heartbeat checker.
    Call start() to begin, stop() to shut down cleanly.
    """

    def __init__(
        self,
        registry: Optional[WorkerRegistry] = None,
        check_interval: int = CHECK_INTERVAL,
    ):
        self._registry = registry or WorkerRegistry()
        self._interval = check_interval
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    def start(self) -> None:
        """Start the background heartbeat check loop."""
        if self._task is not None and not self._task.done():
            logger.warning("HeartbeatMonitor: already running")
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run())
        logger.info(
            "HeartbeatMonitor: started (interval=%ds, stale_threshold=%ds)",
            self._interval,
            int(os.environ.get("HICLAW_STALE_THRESHOLD_SECONDS", "300")),
        )

    async def stop(self) -> None:
        """Stop the background loop gracefully."""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("HeartbeatMonitor: stopped")

    async def _run(self) -> None:
        """Check loop — runs forever until stop() is called."""
        while not self._stopping:
            try:
                marked = await self._registry.mark_stale_workers()
                if marked:
                    logger.warning(
                        "HeartbeatMonitor: marked %d workers offline: %s",
                        len(marked),
                        marked,
                    )
                else:
                    logger.debug("HeartbeatMonitor: all workers healthy")
            except Exception as e:
                logger.error("HeartbeatMonitor: check failed: %s", e)
            await asyncio.sleep(self._interval)
