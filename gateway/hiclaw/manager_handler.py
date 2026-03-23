"""
Worker Message Parser - parses JSON messages from workers in the Manager Matrix room.

Workers send registration and status update messages as JSON payloads.
This parser extracts structured data from those messages.
"""

import json
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class WorkerMessageParser:
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
