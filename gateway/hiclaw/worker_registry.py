"""
Worker Registry - tracks registered hiclaw workers.

Workers register via Matrix messages to the Manager room.
This registry maintains the list of active workers and their status.
"""

import json
import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional, List
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class WorkerInfo:
    id: str
    name: str
    capabilities: List[str]
    status: str  # registered, ready, busy, done, error, offline
    version: str
    matrix_user_id: str
    device_id: str
    registered_at: str
    last_seen_at: str
    room_id: Optional[str] = None
    metadata: Optional[Dict] = None


class WorkerRegistry:
    def __init__(self, state_dir: str = "~/.hermes/hiclaw"):
        self.state_path = Path(state_dir).expanduser() / "workers-registry.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._workers: Dict[str, WorkerInfo] = {}
        self._load()

    def _load(self):
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text())
                for w in data.get("workers", []):
                    self._workers[w["id"]] = WorkerInfo(**w)
            except Exception as e:
                logger.warning("Failed to load worker registry: %s", e)

    def _save(self):
        data = {"workers": [asdict(w) for w in self._workers.values()]}
        self.state_path.write_text(json.dumps(data, indent=2))

    async def register_worker(
        self,
        worker_id: str,
        name: str,
        capabilities: List[str],
        version: str,
        matrix_user_id: str,
        device_id: str,
        room_id: str = None,
    ) -> WorkerInfo:
        async with self._lock:
            now = datetime.utcnow().isoformat() + "Z"
            worker = WorkerInfo(
                id=worker_id,
                name=name,
                capabilities=capabilities,
                status="registered",
                version=version,
                matrix_user_id=matrix_user_id,
                device_id=device_id,
                registered_at=now,
                last_seen_at=now,
                room_id=room_id,
            )
            self._workers[worker_id] = worker
            self._save()
            return worker

    async def update_status(
        self, worker_id: str, status: str, message: str = None
    ) -> Optional[WorkerInfo]:
        async with self._lock:
            if worker_id not in self._workers:
                return None
            worker = self._workers[worker_id]
            worker.status = status
            worker.last_seen_at = datetime.utcnow().isoformat() + "Z"
            if message:
                worker.metadata = {"last_message": message}
            self._save()
            return worker

    async def get_worker(self, worker_id: str) -> Optional[WorkerInfo]:
        return self._workers.get(worker_id)

    async def list_workers(self, status: str = None) -> List[WorkerInfo]:
        if status:
            return [w for w in self._workers.values() if w.status == status]
        return list(self._workers.values())

    async def remove_worker(self, worker_id: str) -> bool:
        async with self._lock:
            if worker_id in self._workers:
                del self._workers[worker_id]
                self._save()
                return True
            return False

    async def heartbeat(self, worker_id: str) -> Optional[WorkerInfo]:
        # Preserve current status when refreshing last_seen_at
        worker = self._workers.get(worker_id)
        if not worker:
            return None
        return await self.update_status(worker_id, worker.status)
