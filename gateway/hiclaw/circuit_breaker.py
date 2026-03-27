"""
Circuit breaker pattern implementation for Docker Manager operations.

Prevents cascading failures by stopping Docker operations when the circuit
is open, allowing time for recovery.
"""

import logging
import threading
import time
from enum import Enum
from functools import wraps
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing fast - reject calls
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open and call is rejected."""

    def __init__(
        self, message: str = "Circuit breaker open, Docker operations suspended"
    ):
        self.message = message
        super().__init__(self.message)


class CircuitBreaker:
    """
    Circuit breaker for Docker operations.

    States:
    - CLOSED: Normal operation, calls pass through
    - OPEN: Failing fast, calls are rejected with CircuitOpenError
    - HALF_OPEN: Testing recovery, one test call allowed

    Transitions:
    - CLOSED -> OPEN: After failure_threshold failures in failure_window seconds
    - OPEN -> HALF_OPEN: After cooldown_timeout seconds
    - HALF_OPEN -> CLOSED: If test call succeeds
    - HALF_OPEN -> OPEN: If test call fails
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        failure_window: float = 300.0,  # 5 minutes
        cooldown_timeout: float = 300.0,  # 5 minutes
        excluded_exceptions: Tuple[type, ...] = (),
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Identifier for this circuit breaker
            failure_threshold: Number of failures before opening circuit
            failure_window: Time window in seconds to count failures
            cooldown_timeout: Time in seconds before attempting recovery
            excluded_exceptions: Exceptions that should not count as failures
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.failure_window = failure_window
        self.cooldown_timeout = cooldown_timeout
        self.excluded_exceptions = excluded_exceptions

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._failure_timestamps: list[float] = []
        self._last_failure_time: Optional[float] = None
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        """Get current circuit state, checking for timeout-based transitions."""
        with self._lock:
            self._check_transitions()
            return self._state

    def _check_transitions(self) -> None:
        """Check if state transition is needed (must hold lock)."""
        if self._state == CircuitState.OPEN:
            if self._last_failure_time is not None:
                if time.time() - self._last_failure_time >= self.cooldown_timeout:
                    logger.warning(
                        "Circuit breaker '%s' transitioning OPEN -> HALF_OPEN",
                        self.name,
                    )
                    self._state = CircuitState.HALF_OPEN
                    self._failure_count = 0
                    self._failure_timestamps = []

    def get_state(self) -> dict:
        """Get circuit state for health endpoint reporting."""
        with self._lock:
            self._check_transitions()
            return {
                "name": self.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "failure_threshold": self.failure_threshold,
                "failure_window_seconds": self.failure_window,
                "cooldown_timeout_seconds": self.cooldown_timeout,
            }

    def _record_failure(self) -> None:
        """Record a failure (must hold lock)."""
        now = time.time()
        self._failure_timestamps.append(now)
        self._last_failure_time = now

        cutoff = now - self.failure_window
        self._failure_timestamps = [t for t in self._failure_timestamps if t > cutoff]
        self._failure_count = len(self._failure_timestamps)

        if self._failure_count >= self.failure_threshold:
            if self._state == CircuitState.CLOSED:
                logger.warning(
                    "Circuit breaker '%s' transitioning CLOSED -> OPEN "
                    "(%d failures in %.0f seconds)",
                    self.name,
                    self._failure_count,
                    self.failure_window,
                )
                self._state = CircuitState.OPEN

    def _record_success(self) -> None:
        """Record a success (must hold lock)."""
        if self._state == CircuitState.HALF_OPEN:
            logger.info(
                "Circuit breaker '%s' transitioning HALF_OPEN -> CLOSED, Docker operations resumed",
                self.name,
            )
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._failure_timestamps = []
            self._last_failure_time = None

    def _is_failure_excluded(self, exception: Exception) -> bool:
        """Check if exception type is excluded from failure counting."""
        return isinstance(exception, self.excluded_exceptions)

    def call(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        """
        Execute function through circuit breaker.

        Args:
            func: Function to call
            *args: Positional arguments to pass to func
            **kwargs: Keyword arguments to pass to func

        Returns:
            Result of func call

        Raises:
            CircuitOpenError: If circuit is open
            Exception: Any exception from func (after recording as failure)
        """
        with self._lock:
            self._check_transitions()

            if self._state == CircuitState.OPEN:
                logger.error(
                    "Circuit breaker '%s' is OPEN - rejecting call to %s",
                    self.name,
                    func.__name__,
                )
                raise CircuitOpenError()

            if self._state == CircuitState.HALF_OPEN:
                # In half-open, only allow one test call through
                # After that, reject until transition happens
                pass  # Allow the call through

        try:
            result = func(*args, **kwargs)

            with self._lock:
                self._record_success()

            return result

        except Exception as e:
            if self._is_failure_excluded(e):
                raise

            with self._lock:
                self._record_failure()

                # If we just transitioned to OPEN, log it
                if self._state == CircuitState.OPEN:
                    logger.warning(
                        "Circuit breaker '%s' is now OPEN after failure", self.name
                    )

            raise

    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Decorator usage for circuit breaker."""

        @wraps(func)
        def wrapper(*args, **kwargs):
            return self.call(func, *args, **kwargs)

        return wrapper


# Global circuit breaker instance for Docker operations
_docker_circuit_breaker: Optional[CircuitBreaker] = None
_docker_circuit_lock = threading.Lock()


def get_docker_circuit_breaker() -> CircuitBreaker:
    """Get or create the global Docker circuit breaker instance."""
    global _docker_circuit_breaker
    with _docker_circuit_lock:
        if _docker_circuit_breaker is None:
            _docker_circuit_breaker = CircuitBreaker(
                name="docker_manager",
                failure_threshold=3,
                failure_window=300.0,
                cooldown_timeout=300.0,
            )
        return _docker_circuit_breaker


def get_docker_circuit_state() -> dict:
    """
    Get Docker circuit breaker state for health endpoint.

    Returns:
        Dict with circuit name, state, and metrics
    """
    return get_docker_circuit_breaker().get_state()
