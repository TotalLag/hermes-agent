"""Matrix reconnection with exponential backoff.

This module provides:
- ExponentialBackoff: Calculates delays with jitter for retry attempts
- ReconnectionManager: Manages background reconnection threads per platform
- reconnect_matrix(): Attempts to reconnect the Matrix adapter

Environment variables:
    MATRIX_RECONNECT_ENABLED    Set "false" to disable Matrix reconnection (default: true)
    MATRIX_RECONNECT_MAX_DELAY  Maximum delay between retries in seconds (default: 60)
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class ExponentialBackoff:
    """Exponential backoff calculator with jitter.

    Attributes:
        initial_delay: Starting delay in seconds (default: 1.0)
        max_delay: Maximum delay in seconds (default: 60.0)
        multiplier: Multiplier for each attempt (default: 2.0)
        jitter: Random jitter factor 0.0-1.0 (default: 0.1 = 10%)
    """

    def __init__(
        self,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        multiplier: float = 2.0,
        jitter: float = 0.1,
    ):
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.multiplier = multiplier
        self.jitter = jitter
        self._attempt: int = 0

    def reset(self) -> None:
        """Reset the backoff counter."""
        self._attempt = 0

    def get_delay(self) -> float:
        """Calculate the next delay with jitter.

        Returns:
            Delay in seconds to wait before next retry.
        """
        self._attempt += 1
        # Calculate base delay: initial_delay * multiplier^(attempt-1)
        delay = self.initial_delay * (self.multiplier ** (self._attempt - 1))
        # Cap at max_delay
        delay = min(delay, self.max_delay)
        # Add jitter: +/- (jitter * delay) for randomization
        jitter_range = delay * self.jitter
        delay = delay + random.uniform(-jitter_range, jitter_range)
        # Ensure delay is positive
        return max(0.0, delay)

    @property
    def attempt(self) -> int:
        """Return the current attempt number (1-indexed for display)."""
        return self._attempt


class ReconnectionManager:
    """Manages background reconnection attempts for platforms.

    This class runs a background thread that periodically attempts to reconnect
    failed platforms using exponential backoff.

    Attributes:
        max_delay: Maximum delay between retry attempts (default: 60.0)
    """

    def __init__(self, max_delay: float = 60.0):
        self.max_delay = max_delay
        self._threads: Dict[str, threading.Thread] = {}
        self._stop_events: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def start_reconnection(
        self,
        platform_name: str,
        reconnect_func: Callable[[], bool],
        on_success: Optional[Callable[[], None]] = None,
        on_failure: Optional[Callable[[], None]] = None,
    ) -> None:
        """Start a background reconnection thread for a platform.

        Args:
            platform_name: Name of the platform (e.g., "matrix")
            reconnect_func: Function to call that attempts reconnection.
                           Should return True on success, False on failure.
            on_success: Optional callback called when reconnection succeeds.
            on_failure: Optional callback called when reconnection fails.
        """
        with self._lock:
            # Stop existing thread if running
            self.stop_reconnection(platform_name)

            stop_event = threading.Event()
            self._stop_events[platform_name] = stop_event

            def run_reconnect():
                backoff = ExponentialBackoff(
                    initial_delay=1.0,
                    max_delay=self.max_delay,
                    multiplier=2.0,
                    jitter=0.1,
                )
                while not stop_event.is_set():
                    delay = backoff.get_delay()
                    logger.info(
                        "Reconnection: %s connection lost, retrying in %.1fs (attempt %d)",
                        platform_name,
                        delay,
                        backoff.attempt,
                    )
                    if stop_event.wait(timeout=delay):
                        break  # Stop event was set

                    if stop_event.is_set():
                        break

                    try:
                        success = reconnect_func()
                        if success:
                            logger.info(
                                "Reconnection: %s reconnected successfully after %d attempts",
                                platform_name,
                                backoff.attempt,
                            )
                            backoff.reset()
                            if on_success:
                                try:
                                    on_success()
                                except Exception as e:
                                    logger.warning(
                                        "Reconnection: %s on_success callback failed: %s",
                                        platform_name,
                                        e,
                                    )
                            break
                        else:
                            logger.info(
                                "Reconnection: %s attempt %d failed, will retry",
                                platform_name,
                                backoff.attempt,
                            )
                            if on_failure:
                                try:
                                    on_failure()
                                except Exception as e:
                                    logger.warning(
                                        "Reconnection: %s on_failure callback failed: %s",
                                        platform_name,
                                        e,
                                    )
                    except Exception as e:
                        logger.warning(
                            "Reconnection: %s attempt %d raised exception: %s",
                            platform_name,
                            backoff.attempt,
                            e,
                        )
                        if on_failure:
                            try:
                                on_failure()
                            except Exception as callback_e:
                                logger.warning(
                                    "Reconnection: %s on_failure callback failed: %s",
                                    platform_name,
                                    callback_e,
                                )

        thread = threading.Thread(
            target=run_reconnect,
            name=f"reconnect-{platform_name}",
            daemon=True,
        )
        self._threads[platform_name] = thread
        thread.start()
        logger.info("Reconnection: started for %s", platform_name)

    def stop_reconnection(self, platform_name: str) -> None:
        """Stop the reconnection thread for a platform.

        Args:
            platform_name: Name of the platform to stop reconnecting.
        """
        with self._lock:
            stop_event = self._stop_events.get(platform_name)
            if stop_event:
                stop_event.set()
                self._stop_events.pop(platform_name)

            thread = self._threads.get(platform_name)
            if thread:
                thread.join(timeout=5.0)
                self._threads.pop(platform_name)
                logger.info("Reconnection: stopped for %s", platform_name)

    def is_reconnecting(self, platform_name: str) -> bool:
        """Check if a reconnection is in progress for a platform.

        Args:
            platform_name: Name of the platform to check.

        Returns:
            True if a reconnection thread is running for this platform.
        """
        with self._lock:
            thread = self._threads.get(platform_name)
            return thread is not None and thread.is_alive()

    def stop_all(self) -> None:
        """Stop all reconnection threads."""
        with self._lock:
            for platform_name in list(self._threads.keys()):
                self.stop_reconnection(platform_name)


# Global reconnection manager instance
_reconnection_manager: Optional[ReconnectionManager] = None


def get_reconnection_manager() -> ReconnectionManager:
    """Get or create the global ReconnectionManager instance.

    Returns:
        The global ReconnectionManager instance.
    """
    global _reconnection_manager
    if _reconnection_manager is None:
        max_delay = float(os.getenv("MATRIX_RECONNECT_MAX_DELAY", "60"))
        _reconnection_manager = ReconnectionManager(max_delay=max_delay)
    return _reconnection_manager


def reconnect_matrix(
    gateway_runner: Any,
    platform_config: Any,
) -> bool:
    """Attempt to reconnect the Matrix adapter.

    This function creates a new MatrixAdapter and attempts to connect it.

    Args:
        gateway_runner: The GatewayRunner instance to add the adapter to.
        platform_config: The platform configuration for Matrix.

    Returns:
        True if reconnection was successful, False otherwise.
    """
    from gateway.config import Platform
    from gateway.platforms.matrix import MatrixAdapter, check_matrix_requirements

    if not check_matrix_requirements():
        logger.warning(
            "Reconnection: Matrix requirements not met (matrix-nio not installed or credentials not set)"
        )
        return False

    try:
        adapter = MatrixAdapter(platform_config)

        # Set up message and fatal error handlers
        adapter.set_message_handler(gateway_runner._handle_message)
        adapter.set_fatal_error_handler(gateway_runner._handle_adapter_fatal_error)

        # Attempt to connect
        loop = (
            gateway_runner._shutdown_event.loop
            if hasattr(gateway_runner, "_shutdown_event")
            and hasattr(gateway_runner._shutdown_event, "loop")
            else None
        )
        if loop and hasattr(loop, "run_until_complete"):
            success = loop.run_until_complete(adapter.connect())
        else:
            import asyncio

            success = asyncio.get_event_loop().run_until_complete(adapter.connect())

        if success:
            gateway_runner.adapters[Platform.MATRIX] = adapter
            gateway_runner._sync_voice_mode_state_to_adapter(adapter)
            gateway_runner.delivery_router.adapters = gateway_runner.adapters
            logger.info("Reconnection: Matrix reconnected successfully")
            return True
        else:
            logger.warning("Reconnection: Matrix connect() returned False")
            return False

    except Exception as e:
        logger.warning(
            "Reconnection: Matrix reconnection attempt failed: %s",
            e,
        )
        return False


def start_matrix_reconnection(
    gateway_runner: Any,
    platform_config: Any,
) -> None:
    """Start background reconnection for Matrix.

    Uses the global ReconnectionManager to handle retry with exponential backoff.

    Args:
        gateway_runner: The GatewayRunner instance.
        platform_config: The platform configuration for Matrix.
    """
    enabled = os.getenv("MATRIX_RECONNECT_ENABLED", "true").lower()
    if enabled in ("false", "0", "no"):
        logger.info(
            "Reconnection: Matrix reconnection disabled via MATRIX_RECONNECT_ENABLED"
        )
        return

    def do_reconnect() -> bool:
        return reconnect_matrix(gateway_runner, platform_config)

    def on_success():
        from gateway.config import Platform

        if Platform.MATRIX in gateway_runner._failed_platforms:
            del gateway_runner._failed_platforms[Platform.MATRIX]
            logger.info("Reconnection: Removed Matrix from _failed_platforms")

    def on_failure():
        from gateway.config import Platform

        if Platform.MATRIX not in gateway_runner._failed_platforms:
            gateway_runner._failed_platforms[Platform.MATRIX] = {
                "config": platform_config,
                "attempts": 1,
                "next_retry": time.monotonic() + 1,
            }

    manager = get_reconnection_manager()
    manager.start_reconnection(
        platform_name="matrix",
        reconnect_func=do_reconnect,
        on_success=on_success,
        on_failure=on_failure,
    )


def stop_matrix_reconnection() -> None:
    """Stop any ongoing Matrix reconnection attempts."""
    manager = get_reconnection_manager()
    manager.stop_reconnection("matrix")
