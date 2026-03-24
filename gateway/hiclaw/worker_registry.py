"""
<<<<<<< HEAD
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
=======
WorkerRegistry — persistent tracker for hermes-worker-* agents.

State is stored in ~/.hermes/hiclaw/workers-registry.json.
Workers transition through statuses: registered -> ready | busy | done | error -> offline.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path.home() / ".hermes" / "hiclaw" / "workers-registry.json"
STALE_THRESHOLD_SECONDS = int(os.getenv("HICLAW_STALE_THRESHOLD_SECONDS", "300"))


@dataclass
class Worker:
    id: str
    name: str
    capabilities: list[str]
    version: str
    matrix_user_id: str
    device_id: str
    room_id: Optional[str] = None
    status: str = "registered"
    last_seen_at: str = ""
    registered_at: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Worker":
        return cls(**d)


class WorkerRegistry:
    """
    Async registry for tracking worker state.
    All mutations are atomic (read -> modify -> write).
    """

    def __init__(self, registry_path: str = None):
        self._path = Path(registry_path) if registry_path else REGISTRY_PATH
        self._lock = asyncio.Lock()
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def _read(self) -> dict[str, Worker]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text())
            return {k: Worker.from_dict(v) for k, v in data.items()}
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("WorkerRegistry: corrupt registry, starting fresh: %s", e)
            return {}

    async def _write(self, workers: dict[str, Worker]) -> None:
        data = {k: v.to_dict() for k, v in workers.items()}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.rename(self._path)
>>>>>>> 611f419f (feat(hiclaw): Add WorkerRegistry, WorkerMessageParser, HeartbeatMonitor)

    async def register_worker(
        self,
        worker_id: str,
        name: str,
<<<<<<< HEAD
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
=======
        capabilities: list[str],
        version: str,
        matrix_user_id: str,
        device_id: str,
        room_id: Optional[str] = None,
    ) -> Worker:
        """Register a new worker or re-register an existing one."""
        async with self._lock:
            workers = await self._read()
            now = datetime.now(timezone.utc).isoformat()
            existing = workers.get(worker_id)
            if existing:
                existing.last_seen_at = now
                existing.status = "ready"
                existing.metadata = getattr(existing, "metadata", {})
                worker = existing
            else:
                worker = Worker(
                    id=worker_id,
                    name=name,
                    capabilities=capabilities,
                    version=version,
                    matrix_user_id=matrix_user_id,
                    device_id=device_id,
                    room_id=room_id,
                    status="registered",
                    last_seen_at=now,
                    registered_at=now,
                    metadata={},
                )
                workers[worker_id] = worker
            await self._write(workers)
            return worker

    async def heartbeat(self, worker_id: str) -> Optional[Worker]:
        """Refresh last_seen_at for a worker. Does not change status."""
        async with self._lock:
            workers = await self._read()
            if worker_id not in workers:
                return None
            worker = workers[worker_id]
            worker.last_seen_at = datetime.now(timezone.utc).isoformat()
            await self._write(workers)
            return worker

    async def update_status(
        self, worker_id: str, status: str, message: Optional[str] = None
    ) -> Optional[Worker]:
        """Update a worker's status and optionally a message."""
        async with self._lock:
            workers = await self._read()
            if worker_id not in workers:
                return None
            worker = workers[worker_id]
            worker.status = status
            if message is not None:
                worker.metadata["last_message"] = message
            worker.last_seen_at = datetime.now(timezone.utc).isoformat()
            await self._write(workers)
            return worker

    async def remove_worker(self, worker_id: str) -> bool:
        """Remove a worker from the registry."""
        async with self._lock:
            workers = await self._read()
            if worker_id in workers:
                del workers[worker_id]
                await self._write(workers)
                return True
            return False

    async def get_worker(self, worker_id: str) -> Optional[Worker]:
        """Get a single worker by ID."""
        workers = await self._read()
        return workers.get(worker_id)

    async def list_workers(self, status: Optional[str] = None) -> list[Worker]:
        """List all workers, optionally filtered by status."""
        workers = await self._read()
        if status:
            return [w for w in workers.values() if w.status == status]
        return list(workers.values())

    async def mark_stale_workers(self) -> list[str]:
        """Mark workers as offline if last_seen_at is older than STALE_THRESHOLD_SECONDS."""
        async with self._lock:
            workers = await self._read()
            cutoff = datetime.now(timezone.utc).timestamp() - STALE_THRESHOLD_SECONDS
            marked = []
            for worker in workers.values():
                if worker.status != "offline" and worker.last_seen_at:
                    try:
                        last_seen = datetime.fromisoformat(
                            worker.last_seen_at.replace("Z", "+00:00")
                        ).timestamp()
                        if last_seen < cutoff:
                            worker.status = "offline"
                            marked.append(worker.id)
                    except (ValueError, TypeError):
                        pass
            if marked:
                await self._write(workers)
            return marked
>>>>>>> 611f419f (feat(hiclaw): Add WorkerRegistry, WorkerMessageParser, HeartbeatMonitor)
