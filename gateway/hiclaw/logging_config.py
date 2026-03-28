"""Structured JSON logging configuration for HiClaw gateway and MCP servers.

Provides:
- JSONFormatter: outputs {"timestamp": "...", "level": "...", "logger": "...",
                         "message": "...", "extra_fields": {...}}
- setup_logging(): configures root logger with JSONFormatter + optional RotatingFileHandler
- Environment variable support:
  - HICLAW_LOG_LEVEL: DEBUG/INFO/WARNING/ERROR (default INFO)
  - HICLAW_LOG_JSON: true/false (default true)
  - HICLAW_LOG_FILE: file path for file output (optional)
- RedactingFormatter: adapts agent/redact.py redaction logic to JSON format
- Request_id/correlation_id support for MCP call tracing
- DEBUG-level request/response logging for MCP tool calls
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from contextvars import ContextVar
from typing import Optional, Any

# Context variable for request/correlation ID tracing
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
correlation_id_var: ContextVar[Optional[str]] = ContextVar(
    "correlation_id", default=None
)


def get_request_id() -> Optional[str]:
    """Get current request ID from context."""
    return request_id_var.get()


def get_correlation_id() -> Optional[str]:
    """Get current correlation ID from context."""
    return correlation_id_var.get()


def set_request_id(req_id: Optional[str] = None) -> str:
    """Set request ID in context. Generates one if not provided."""
    if req_id is None:
        req_id = str(uuid.uuid4())[:8]
    request_id_var.set(req_id)
    return req_id


def set_correlation_id(corr_id: Optional[str] = None) -> str:
    """Set correlation ID in context. Generates one if not provided."""
    if corr_id is None:
        corr_id = str(uuid.uuid4())[:8]
    correlation_id_var.set(corr_id)
    return corr_id


def clear_request_context():
    """Clear request context (use after request completes)."""
    request_id_var.set(None)
    correlation_id_var.set(None)


# Import redaction logic from agent.redact
try:
    from agent.redact import redact_sensitive_text
except ImportError:
    # Fallback if agent.redact is not available
    def redact_sensitive_text(text: str) -> str:
        return text


class JSONFormatter(logging.Formatter):
    """JSON log formatter with secret redaction and extra field support.

    Outputs structured JSON:
    {
        "timestamp": "2024-01-01T12:00:00.000Z",
        "level": "INFO",
        "logger": "module.name",
        "message": "log message",
        "request_id": "abc123",
        "correlation_id": "xyz789",
        "extra_fields": {...}
    }
    """

    def __init__(
        self,
        fmt=None,
        datefmt=None,
        style="%",
        redact: bool = True,
        include_extra: bool = True,
    ):
        super().__init__(fmt, datefmt, style)  # type: ignore[arg-type]
        self.redact = redact
        self.include_extra = include_extra
        # Use ISO format with timezone
        self.default_time_format = "%Y-%m-%dT%H:%M:%S.%f"
        self.default_msec_format = "%s.%03dZ"

    def _format_timestamp(self, record: logging.LogRecord) -> str:
        """Format timestamp in ISO 8601 format with UTC timezone."""
        ct = datetime.fromtimestamp(record.created, tz=timezone.utc)
        if record.msecs:
            return (
                ct.strftime("%Y-%m-%dT%H:%M:%S.") + f"{record.msecs:.0f}".zfill(3) + "Z"
            )
        return ct.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _redact_message(self, msg: str) -> str:
        """Redact sensitive data from message."""
        if self.redact:
            return redact_sensitive_text(msg)
        return msg

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON string."""
        # Get request/correlation IDs from context
        request_id = get_request_id()
        correlation_id = get_correlation_id()

        # Build base log entry
        log_entry: dict[str, Any] = {
            "timestamp": self._format_timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": self._redact_message(record.getMessage()),
        }

        # Add request tracing IDs if present
        if request_id:
            log_entry["request_id"] = request_id
        if correlation_id:
            log_entry["correlation_id"] = correlation_id

        # Add extra fields from record
        if self.include_extra:
            extra_fields: dict[str, Any] = {}
            # Standard record attributes
            for key in ("filename", "lineno", "funcName", "processName", "threadName"):
                value = getattr(record, key, None)
                if value is not None:
                    extra_fields[key] = value

            # Custom extra fields added via record.extra
            extra_fields_val = getattr(record, "extra_fields", None)
            if extra_fields_val:
                extra_fields.update(extra_fields_val)

            # MCP-specific fields
            mcp_method = getattr(record, "mcp_method", None)
            if mcp_method is not None:
                extra_fields["mcp_method"] = mcp_method
            mcp_params = getattr(record, "mcp_params", None)
            if mcp_params is not None:
                extra_fields["mcp_params"] = mcp_params
            mcp_result = getattr(record, "mcp_result", None)
            if mcp_result is not None:
                extra_fields["mcp_result"] = mcp_result

            if extra_fields:
                log_entry["extra_fields"] = extra_fields

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


class RedactingJSONFormatter(JSONFormatter):
    """JSON formatter that always redacts sensitive data."""

    def __init__(self, fmt=None, datefmt=None, style="%"):
        super().__init__(fmt, datefmt, style, redact=True)


def _get_log_level() -> int:
    """Get log level from HICLAW_LOG_LEVEL env var."""
    level_str = os.getenv("HICLAW_LOG_LEVEL", "INFO").upper()
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return level_map.get(level_str, logging.INFO)


def _use_json() -> bool:
    """Determine if JSON format should be used."""
    json_str = os.getenv("HICLAW_LOG_JSON", "true").lower()
    return json_str not in ("0", "false", "no", "off")


def _get_log_file() -> Optional[str]:
    """Get log file path from HICLAW_LOG_FILE env var."""
    return os.getenv("HICLAW_LOG_FILE")


def setup_logging(
    level: Optional[int] = None,
    use_json: Optional[bool] = None,
    log_file: Optional[str] = None,
    propagate: bool = False,
) -> logging.Logger:
    """Configure structured JSON logging for the application.

    Args:
        level: Log level (default: from HICLAW_LOG_LEVEL or INFO)
        use_json: Whether to use JSON format (default: from HICLAW_LOG_JSON or true)
        log_file: Optional log file path (default: from HICLAW_LOG_FILE)
        propagate: Whether to propagate to root logger handlers (default: False)

    Returns:
        Configured root logger
    """
    # Resolve settings from env vars if not provided
    if level is None:
        level = _get_log_level()
    if use_json is None:
        use_json = _use_json()
    if log_file is None:
        log_file = _get_log_file()

    # Get or create root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers (unless propagating)
    if not propagate:
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    # Create formatter
    if use_json:
        formatter = JSONFormatter(redact=True)
    else:
        # Use redacting plain text formatter for non-JSON
        from agent.redact import RedactingFormatter

        formatter = RedactingFormatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        )

    # Console handler (stdout for container log aggregation)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler if log_file is specified
    if log_file:
        # Ensure directory exists
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    return root_logger


class MCPLoggingAdapter(logging.LoggerAdapter):
    """Logger adapter that adds MCP request/response context for DEBUG logging.

    Use this to wrap loggers that will be used in MCP tool call handling
    to enable automatic DEBUG-level request/response logging.
    """

    def process(self, msg, kwargs):
        """Add MCP context to log message."""
        extra = kwargs.get("extra", {})

        # Add request/correlation IDs from context
        req_id = get_request_id()
        corr_id = get_correlation_id()
        if req_id:
            extra["request_id"] = req_id
        if corr_id:
            extra["correlation_id"] = corr_id

        kwargs["extra"] = extra
        return msg, kwargs


def log_mcp_call(
    logger: logging.Logger,
    method: str,
    params: Any,
    result: Any = None,
    error: Optional[Exception] = None,
):
    """Log an MCP tool call at DEBUG level.

    Args:
        logger: Logger instance to use
        method: MCP method name
        params: Method parameters (will be redacted)
        result: Method result (will be redacted)
        error: Optional exception if call failed
    """
    if not logger.isEnabledFor(logging.DEBUG):
        return

    # Redact sensitive data
    redacted_params = redact_sensitive_text(str(params))
    redacted_result = redact_sensitive_text(str(result)) if result is not None else None

    extra = {
        "mcp_method": method,
        "mcp_params": redacted_params,
    }
    if redacted_result is not None:
        extra["mcp_result"] = redacted_result
    if error:
        extra["mcp_error"] = str(error)

    logger.debug(f"MCP call: {method}", extra={"extra_fields": extra})


# Convenience function for setting up MCP server logging
def setup_mcp_logging(
    name: str,
    level: Optional[int] = None,
    use_json: Optional[bool] = None,
) -> logging.Logger:
    """Set up logging for an MCP server.

    Args:
        name: Logger name (typically __name__)
        level: Log level (default: from HICLAW_LOG_LEVEL or INFO)
        use_json: Whether to use JSON format (default: from HICLAW_LOG_JSON or true)

    Returns:
        Configured logger
    """
    if level is None:
        level = _get_log_level()
    if use_json is None:
        use_json = _use_json()

    # Set up root logging first
    setup_logging(level=level, use_json=use_json, propagate=True)

    # Return logger with given name
    return logging.getLogger(name)
