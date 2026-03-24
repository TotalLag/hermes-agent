"""
<<<<<<< HEAD
Worker Message Parser - parses JSON messages from workers in the Manager Matrix room.

Workers send registration and status update messages as JSON payloads.
This parser extracts structured data from those messages.
=======
WorkerMessageParser — parses incoming worker messages to detect type and extract data.

Handles three message types:
  - Registration: JSON payload sent on worker boot
  - Status update: JSON status change notification
  - Heartbeat: natural language with //heartbeat prefix
>>>>>>> 611f419f (feat(hiclaw): Add WorkerRegistry, WorkerMessageParser, HeartbeatMonitor)
"""

import json
import logging
<<<<<<< HEAD
from typing import Optional, Tuple
=======
import re
from typing import Optional
>>>>>>> 611f419f (feat(hiclaw): Add WorkerRegistry, WorkerMessageParser, HeartbeatMonitor)

logger = logging.getLogger(__name__)


class WorkerMessageParser:
<<<<<<< HEAD
    """Parses JSON messages from workers in the Manager Matrix room."""

    @staticmethod
    def parse_registration(content: str) -> Optional[dict]:
        """Parse worker registration message."""
        try:
            data = json.loads(content)
            required = [
                "id",
                "name",
                "capabilities",
                "status",
                "version",
                "matrix_user_id",
                "device_id",
            ]
            if all(k in data for k in required):
                return data
            logger.warning("Registration missing required fields: %s", data)
            return None
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse registration: %s - %s", e, content)
            return None

    @staticmethod
    def parse_status(
        content: str,
    ) -> Optional[Tuple[str, str, Optional[str]]]:
        """Parse status update message. Returns (status, worker_name, message)."""
        try:
            data = json.loads(content)
            status = data.get("status")
            worker = data.get("worker", "unknown")
            message = data.get("message")
            if status:
                return (status, worker, message)
            logger.warning("Status update missing status: %s", data)
            return None
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse status: %s - %s", e, content)
            return None

    @staticmethod
    def is_worker_message(content: str) -> bool:
        """Check if message appears to be a worker message."""
        try:
            data = json.loads(content)
            # Worker messages have these indicators
            return ("status" in data and "worker" in data) or (
                "id" in data and "capabilities" in data and "status" in data
            )
        except json.JSONDecodeError:
            return False
=======
    """Static methods for parsing raw worker messages."""

    @staticmethod
    def is_worker_message(content: str) -> bool:
        """Return True if the content appears to be a worker message."""
        if content is None:
            return False
        stripped = content.strip()
        return (
            stripped.startswith("//heartbeat")
            or stripped.startswith("{")
            or stripped.startswith("//task-assign")
            or stripped.startswith("//task-result")
        )

    @staticmethod
    def parse_registration(content: str) -> Optional[dict]:
        """
        Parse a worker registration JSON payload.

        Expected fields:
          id, name, capabilities, status, version,
          matrix_user_id, device_id

        Returns None if parsing fails.
        """
        if content is None:
            return None
        try:
            data = json.loads(content)
            required = ("id", "name", "capabilities", "version", "matrix_user_id")
            if not all(k in data for k in required):
                return None
            return {
                "id": str(data["id"]),
                "name": str(data["name"]),
                "capabilities": list(data["capabilities"])
                if isinstance(data["capabilities"], list)
                else [],
                "status": str(data.get("status", "registered")),
                "version": str(data["version"]),
                "matrix_user_id": str(data["matrix_user_id"]),
                "device_id": str(data.get("device_id", "")),
            }
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.debug("WorkerMessageParser: registration parse failed: %s", e)
            return None

    @staticmethod
    def parse_status(content: str) -> Optional[tuple[str, str, str]]:
        """
        Parse a worker status update JSON payload.

        Returns (worker_id, status, message) or None.
        Expected JSON: {"status": "...", "worker": "...", "message": "..."}
        """
        if content is None:
            return None
        try:
            data = json.loads(content)
            if "status" not in data or "worker" not in data:
                return None
            return (
                str(data["worker"]),
                str(data["status"]),
                str(data.get("message", "")),
            )
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.debug("WorkerMessageParser: status parse failed: %s", e)
            return None

    @staticmethod
    def parse_heartbeat(content: str) -> Optional[dict]:
        """
        Parse a //heartbeat natural language message.

        Format:
          //heartbeat
          Worker: {name}
          Status: {ready|busy}
          Last completed task: {task_id}
          Current task: {task_id}    # optional, when busy
          Progress: {percent}%      # optional, when busy

        Returns dict with worker_name, status, last_completed_task, current_task, progress.
        """
        if content is None:
            return None
        stripped = content.strip()
        if not stripped.startswith("//heartbeat"):
            return None
        body = stripped[len("//heartbeat") :].strip()
        result: dict = {}
        for line in body.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip().lower()
            val = val.strip()
            if key == "worker":
                result["worker_name"] = val
            elif key == "status":
                result["status"] = val
            elif key == "last completed task":
                result["last_completed_task"] = val
            elif key == "current task":
                result["current_task"] = val
            elif key == "progress":
                m = re.match(r"(\d+)", val)
                result["progress"] = int(m.group(1)) if m else None
        if "worker_name" not in result:
            return None
        return {
            "worker_name": result.get("worker_name", ""),
            "status": result.get("status", ""),
            "last_completed_task": result.get("last_completed_task"),
            "current_task": result.get("current_task"),
            "progress": result.get("progress"),
        }

    @staticmethod
    def parse_task_assign(content: str) -> Optional[dict]:
        """
        Parse a //task-assign natural language message from Manager to Worker.

        Format:
          //task-assign
          Task ID: {task_id}
          Spec file: {path}    # optional
          {natural language description}

        Returns dict with task_id, spec_path (or None), and description.
        """
        if content is None:
            return None
        stripped = content.strip()
        if not stripped.startswith("//task-assign"):
            return None
        body = stripped[len("//task-assign") :].strip()
        task_id = None
        spec_path = None
        remaining_lines: list[str] = []
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith("task id:"):
                task_id = line.partition(":")[2].strip()
            elif line.lower().startswith("spec file:"):
                spec_path = line.partition(":")[2].strip()
            else:
                remaining_lines.append(line)
        if not task_id:
            return None
        return {
            "task_id": task_id,
            "spec_path": spec_path,
            "description": "\n".join(remaining_lines).strip(),
        }

    @staticmethod
    def parse_task_result(content: str) -> Optional[dict]:
        """
        Parse a //task-result natural language message from Worker to Manager.

        Format:
          //task-result
          Task ID: {task_id}
          Status: completed | failed
          {natural language result or error message}

        Returns dict with task_id, status ("completed" or "failed"),
        and result_text.
        """
        if content is None:
            return None
        stripped = content.strip()
        if not stripped.startswith("//task-result"):
            return None
        body = stripped[len("//task-result") :].strip()
        task_id = None
        status = None
        remaining_lines: list[str] = []
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith("task id:"):
                task_id = line.partition(":")[2].strip()
            elif line.lower().startswith("status:"):
                status = line.partition(":")[2].strip().lower()
            else:
                remaining_lines.append(line)
        if not task_id or not status:
            return None
        if status not in ("completed", "failed"):
            return None
        return {
            "task_id": task_id,
            "status": status,
            "result_text": "\n".join(remaining_lines).strip(),
        }

    @staticmethod
    def detect_message_type(content: str) -> str:
        if content is None:
            return "unknown"
        stripped = content.strip()
        if stripped.startswith("//task-assign"):
            return "task_assign"
        if stripped.startswith("//task-result"):
            return "task_result"
        if stripped.startswith("//heartbeat"):
            return "heartbeat"
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
                if "id" in data and "name" in data:
                    return "registration"
                if "worker" in data and "status" in data:
                    return "status"
            except Exception:
                pass
        return "unknown"
>>>>>>> 611f419f (feat(hiclaw): Add WorkerRegistry, WorkerMessageParser, HeartbeatMonitor)
