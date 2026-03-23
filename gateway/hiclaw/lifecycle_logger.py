"""
Lifecycle Logger - records worker lifecycle events for audit trail.

Tracks registration, status changes, heartbeats, and removals.
Keeps last 1000 events for audit purposes.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class LifecycleEvent:
    event_id: str
    worker_id: str
    worker_name: str
    event_type: str  # registered, status_change, heartbeat, removed
    details: Dict
    timestamp: str


class LifecycleLogger:
    def __init__(self, state_dir: str = "~/.hermes/hiclaw"):
        self.events_path = Path(state_dir).expanduser() / "worker-lifecycle.json"
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self._events: List[Dict] = []
        self._load()

    def _load(self):
        if self.events_path.exists():
            try:
                self._events = json.loads(self.events_path.read_text())
            except Exception as e:
                logger.warning("Failed to load lifecycle events: %s", e)
                self._events = []
        # Keep last 1000 events
        if len(self._events) > 1000:
            self._events = self._events[-1000:]

    def _save(self):
        self.events_path.write_text(json.dumps(self._events, indent=2))

    def log_event(
        self,
        worker_id: str,
        worker_name: str,
        event_type: str,
        details: Dict = None,
    ):
        event = LifecycleEvent(
            event_id=f"{worker_id}-{datetime.utcnow().timestamp()}",
            worker_id=worker_id,
            worker_name=worker_name,
            event_type=event_type,
            details=details or {},
            timestamp=datetime.utcnow().isoformat() + "Z",
        )
        self._events.append(asdict(event))
        self._save()

    def log_registration(
        self, worker_id: str, worker_name: str, capabilities: List[str], version: str
    ):
        self.log_event(
            worker_id,
            worker_name,
            "registered",
            {"capabilities": capabilities, "version": version},
        )

    def log_status_change(
        self,
        worker_id: str,
        worker_name: str,
        old_status: str,
        new_status: str,
        message: str = None,
    ):
        self.log_event(
            worker_id,
            worker_name,
            "status_change",
            {"old_status": old_status, "new_status": new_status, "message": message},
        )

    def log_removal(self, worker_id: str, worker_name: str, reason: str = None):
        self.log_event(worker_id, worker_name, "removed", {"reason": reason})

    def get_events(self, worker_id: str = None, limit: int = 100) -> List[Dict]:
        if worker_id:
            events = [e for e in self._events if e["worker_id"] == worker_id]
        else:
            events = self._events
        return events[-limit:]

    def get_worker_history(self, worker_id: str) -> List[Dict]:
        return [e for e in self._events if e["worker_id"] == worker_id]
