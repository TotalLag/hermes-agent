"""
Manager State - tracks Manager mode, tasks, and statistics.

The Manager assigns tasks to workers and tracks their completion.
"""

import json
import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional, List, Any
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class ManagerMode(Enum):
    IDLE = "idle"
    DISPATCHING = "dispatching"
    MONITORING = "monitoring"


@dataclass
class TaskInfo:
    id: str
    spec_path: str
    assigned_worker: Optional[str]
    status: str  # pending, assigned, running, completed, failed
    created_at: str
    updated_at: str
    result_path: Optional[str] = None
    error: Optional[str] = None


class ManagerState:
    def __init__(self, state_dir: str = "~/.hermes/hiclaw"):
        self.state_path = Path(state_dir).expanduser() / "state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._state: Dict[str, Any] = {}
        self._load()

    def _load(self):
        if self.state_path.exists():
            try:
                self._state = json.loads(self.state_path.read_text())
            except Exception as e:
                logger.warning("Failed to load manager state: %s", e)
                self._reset_state()
        else:
            self._reset_state()

    def _reset_state(self):
        self._state = {
            "mode": ManagerMode.IDLE.value,
            "manager_id": "hermes-manager",
            "tasks": {},
            "stats": {
                "total_tasks": 0,
                "completed_tasks": 0,
                "failed_tasks": 0,
            },
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }

    def _save(self):
        self._state["updated_at"] = datetime.utcnow().isoformat() + "Z"
        self.state_path.write_text(json.dumps(self._state, indent=2))

    async def set_mode(self, mode: ManagerMode):
        async with self._lock:
            self._state["mode"] = mode.value
            self._save()

    async def get_mode(self) -> ManagerMode:
        return ManagerMode(self._state.get("mode", ManagerMode.IDLE.value))

    async def add_task(self, task_id: str, spec_path: str) -> TaskInfo:
        async with self._lock:
            now = datetime.utcnow().isoformat() + "Z"
            task = TaskInfo(
                id=task_id,
                spec_path=spec_path,
                assigned_worker=None,
                status="pending",
                created_at=now,
                updated_at=now,
            )
            self._state["tasks"][task_id] = asdict(task)
            self._state["stats"]["total_tasks"] += 1
            self._save()
            return task

    async def assign_task(self, task_id: str, worker_id: str) -> Optional[TaskInfo]:
        async with self._lock:
            if task_id not in self._state["tasks"]:
                return None
            task = self._state["tasks"][task_id]
            task["assigned_worker"] = worker_id
            task["status"] = "assigned"
            task["updated_at"] = datetime.utcnow().isoformat() + "Z"
            self._save()
            return TaskInfo(**task)

    async def complete_task(self, task_id: str, result_path: str) -> Optional[TaskInfo]:
        async with self._lock:
            if task_id not in self._state["tasks"]:
                return None
            task = self._state["tasks"][task_id]
            task["status"] = "completed"
            task["result_path"] = result_path
            task["updated_at"] = datetime.utcnow().isoformat() + "Z"
            self._state["stats"]["completed_tasks"] += 1
            self._save()
            return TaskInfo(**task)

    async def fail_task(self, task_id: str, error: str) -> Optional[TaskInfo]:
        async with self._lock:
            if task_id not in self._state["tasks"]:
                return None
            task = self._state["tasks"][task_id]
            task["status"] = "failed"
            task["error"] = error
            task["updated_at"] = datetime.utcnow().isoformat() + "Z"
            self._state["stats"]["failed_tasks"] += 1
            self._save()
            return TaskInfo(**task)

    async def get_task(self, task_id: str) -> Optional[TaskInfo]:
        if task_id in self._state.get("tasks", {}):
            return TaskInfo(**self._state["tasks"][task_id])
        return None

    async def list_tasks(self, status: str = None) -> List[TaskInfo]:
        tasks = [TaskInfo(**t) for t in self._state.get("tasks", {}).values()]
        if status:
            tasks = [t for t in tasks if t.status == status]
        return tasks

    async def get_stats(self) -> Dict[str, int]:
        return self._state.get("stats", {})
