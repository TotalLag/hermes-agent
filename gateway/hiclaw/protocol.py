"""hiClaw protocol parsing and encoding helpers.

This module handles the lightweight natural-language protocol used between the
hiClaw manager and workers over Matrix, along with the JSON worker messages
used for registration and status updates.

Supported message markers:

* ``//task-assign`` — manager to worker task assignment
* ``//task-result`` — worker to manager task result
* ``//heartbeat`` — worker to manager heartbeat
"""

from __future__ import annotations

import json
from typing import Any

TASK_ASSIGN_MARKER = "//task-assign"
TASK_RESULT_MARKER = "//task-result"
HEARTBEAT_MARKER = "//heartbeat"

TASK_ID_FIELD = "Task ID"
STATUS_FIELD = "Status"
WORKER_FIELD = "Worker"
MESSAGE_FIELD = "Message"

VALID_TASK_RESULT_STATUSES = {"completed", "failed"}
REGISTRATION_FIELDS = (
    "id",
    "name",
    "capabilities",
    "status",
    "version",
    "matrix_user_id",
    "device_id",
)


def _strip_content(content: str) -> str:
    return content.strip() if isinstance(content, str) else ""


def _parse_json_dict(content: str) -> dict[str, Any] | None:
    stripped = _strip_content(content)
    if not stripped:
        return None

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None

    return data if isinstance(data, dict) else None


def _is_non_empty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)

    return value is not None


def _non_empty_string(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if not isinstance(value, str):
        return None

    value = value.strip()
    return value or None


def _parse_tagged_message(
    content: str,
    marker: str,
    header_names: tuple[str, ...],
) -> tuple[dict[str, str], str] | None:
    stripped = _strip_content(content)
    if not stripped:
        return None

    lines = stripped.splitlines()
    if not lines or lines[0].strip() != marker:
        return None

    header_lookup = {name.lower(): name for name in header_names}
    headers: dict[str, str] = {}
    body_lines: list[str] = []
    in_body = False

    for raw_line in lines[1:]:
        line = raw_line.strip()

        if in_body:
            body_lines.append(raw_line)
            continue

        if not line:
            if headers:
                in_body = True
            continue

        key, separator, value = line.partition(":")
        canonical_key = header_lookup.get(key.strip().lower()) if separator else None
        if canonical_key is not None:
            headers[canonical_key] = value.strip()
            continue

        in_body = True
        body_lines.append(raw_line)

    return headers, "\n".join(body_lines).strip()


class WorkerMessageParser:
    """Static helpers for parsing hiClaw worker messages."""

    @staticmethod
    def is_worker_message(content: str) -> bool:
        """Return ``True`` when *content* looks like a worker-originated message.

        This check is intentionally lenient and accepts:

        * ``//heartbeat`` natural-language messages
        * ``//task-result`` natural-language messages
        * JSON status updates with ``status`` and ``worker`` keys
        * JSON registrations with ``id``, ``capabilities``, and ``status`` keys
        * legacy JSON heartbeats with ``type=worker.heartbeat``
        """

        stripped = _strip_content(content)
        if not stripped:
            return False

        first_line = stripped.splitlines()[0].strip()
        if first_line in {HEARTBEAT_MARKER, TASK_RESULT_MARKER}:
            return True

        data = _parse_json_dict(stripped)
        if data is None:
            return False

        if all(key in data for key in ("id", "capabilities", "status")):
            return True

        if all(key in data for key in ("status", "worker")):
            return True

        return (
            data.get("type") == "worker.heartbeat"
            and _is_non_empty(data.get("worker_id") or data.get("worker"))
            and _is_non_empty(data.get("status"))
        )

    @staticmethod
    def parse_registration(content: str) -> dict[str, Any] | None:
        """Parse a worker registration JSON payload.

        Required keys are: ``id``, ``name``, ``capabilities``, ``status``,
        ``version``, ``matrix_user_id``, and ``device_id``. All must be present
        and non-empty.
        """

        data = _parse_json_dict(content)
        if data is None:
            return None

        registration: dict[str, Any] = {}
        for key in REGISTRATION_FIELDS:
            value = data.get(key)
            if not _is_non_empty(value):
                return None
            registration[key] = value.strip() if isinstance(value, str) else value

        return registration

    @staticmethod
    def parse_status(content: str) -> tuple[str, str, str] | None:
        """Parse a JSON worker status update.

        Expected payload:
        ``{"status": "...", "worker": "...", "message": "..."}``
        """

        data = _parse_json_dict(content)
        if data is None:
            return None

        status = _non_empty_string(data, "status")
        worker_name = _non_empty_string(data, "worker")
        message = data.get("message")

        if status is None or worker_name is None or not isinstance(message, str):
            return None

        return status, worker_name, message

    @staticmethod
    def parse_task_result(content: str) -> dict[str, str] | None:
        """Parse a ``//task-result`` message.

        Returns a mapping with ``task_id``, ``status``, and ``body`` when valid.
        """

        parsed = _parse_tagged_message(
            content, TASK_RESULT_MARKER, (TASK_ID_FIELD, STATUS_FIELD)
        )
        if parsed is None:
            return None

        headers, body = parsed
        task_id = headers.get(TASK_ID_FIELD, "").strip()
        status = headers.get(STATUS_FIELD, "").strip().lower()

        if not task_id or status not in VALID_TASK_RESULT_STATUSES:
            return None

        return {"task_id": task_id, "status": status, "body": body}

    @staticmethod
    def parse_task_assign(content: str) -> dict[str, str] | None:
        """Parse a ``//task-assign`` message."""

        parsed = _parse_tagged_message(content, TASK_ASSIGN_MARKER, (TASK_ID_FIELD,))
        if parsed is None:
            return None

        headers, body = parsed
        task_id = headers.get(TASK_ID_FIELD, "").strip()
        if not task_id:
            return None

        return {"task_id": task_id, "body": body}

    @staticmethod
    def parse_heartbeat(content: str) -> dict[str, str] | None:
        """Parse a worker heartbeat message.

        Supports the natural-language ``//heartbeat`` protocol and the legacy
        JSON heartbeat payload format.
        """

        parsed = _parse_tagged_message(
            content,
            HEARTBEAT_MARKER,
            (WORKER_FIELD, STATUS_FIELD, MESSAGE_FIELD),
        )
        if parsed is not None:
            headers, body = parsed
            worker = headers.get(WORKER_FIELD, "").strip()
            status = headers.get(STATUS_FIELD, "").strip()
            message_parts = [headers.get(MESSAGE_FIELD, "").strip(), body]
            message = "\n\n".join(part for part in message_parts if part)

            if worker and status:
                return {"worker": worker, "status": status, "message": message}

        data = _parse_json_dict(content)
        if data is None or data.get("type") != "worker.heartbeat":
            return None

        worker = data.get("worker") or data.get("worker_id") or data.get("id")
        status = data.get("status")
        message = data.get("message", "")

        if not _is_non_empty(worker) or not _is_non_empty(status):
            return None

        return {
            "worker": str(worker).strip(),
            "status": str(status).strip(),
            "message": str(message).strip() if message is not None else "",
        }


class TaskAssignEncoder:
    """Encoder for manager ``//task-assign`` messages."""

    @staticmethod
    def encode(task_id: str, body: str) -> str:
        """Encode a ``//task-assign`` message.

        Output format::

            //task-assign

            Task ID: {task_id}

            {body}
        """

        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")

        body_text = body.strip() if isinstance(body, str) else ""
        return (
            f"{TASK_ASSIGN_MARKER}\n\n{TASK_ID_FIELD}: {task_id.strip()}\n\n{body_text}"
        )


__all__ = ["TaskAssignEncoder", "WorkerMessageParser"]
