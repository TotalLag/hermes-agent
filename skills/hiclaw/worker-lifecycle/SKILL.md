---
name: hiclaw-worker-lifecycle
description: Manage worker lifecycle events — registration, heartbeat, status updates, and deregistration — via the WorkerRegistry data model. Workers send JSON messages to the Manager Matrix room; the Manager parses and updates registry state accordingly.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [HiClaw, Worker, Lifecycle, Registry, Matrix, Heartbeat]
---

# Worker Lifecycle — Register, Heartbeat, Deregister

Workers move through a defined lifecycle: `registered` → `ready` / `busy` / `done` / `error` → `offline`. The `WorkerRegistry` class (`skills/hiclaw/worker_registry.py`) tracks all state persistently in `~/.hermes/hiclaw/workers-registry.json`.

## Worker Status Values

| Status | Meaning |
|--------|---------|
| `registered` | Worker has joined; initial state after first boot |
| `ready` | Worker is idle and can accept tasks |
| `busy` | Worker is executing a task |
| `done` | Worker completed its current task and is ready for more |
| `error` | Worker encountered an error |
| `offline` | Worker has not sent a heartbeat within the check window |

## Registration

Workers self-register by sending a JSON payload to the Manager Matrix room. The Manager parses it with `WorkerMessageParser.parse_registration()`.

```python
# skills/hiclaw/worker_registry.py — WorkerRegistry.register_worker()
from skills.hiclaw.worker_registry import WorkerRegistry

registry = WorkerRegistry()
worker = await registry.register_worker(
    worker_id="hermes-worker-alice",
    name="alice",
    capabilities=["coding", "research", "file-ops"],
    version="1.0.0",
    matrix_user_id="@alice:matrix.example.com",
    device_id="DEVICEABC123",
    room_id="!manager-room:matrix.example.com",
)
# worker.status == "registered"
```

Required JSON fields for registration (from `WorkerMessageParser.parse_registration()`):

```json
{
  "id": "hermes-worker-alice",
  "name": "alice",
  "capabilities": ["coding", "research"],
  "status": "registered",
  "version": "1.0.0",
  "matrix_user_id": "@alice:matrix.example.com",
  "device_id": "DEVICEABC123"
}
```

## Heartbeat

Workers send a heartbeat every **2 minutes**. The Manager checks heartbeats every **5 minutes**. The heartbeat updates `last_seen_at` but preserves the worker's current status.

```python
# skills/hiclaw/worker_registry.py — WorkerRegistry.heartbeat()
worker = await registry.heartbeat("hermes-worker-alice")
# worker.last_seen_at refreshed; worker.status unchanged
```

The heartbeat flow:
1. Worker sends `//heartbeat` message (or JSON status ping) to Manager room
2. Manager calls `registry.heartbeat(worker_id)`
3. `last_seen_at` is updated to current UTC time
4. Worker status remains whatever it was (`ready`, `busy`, etc.)

## Status Updates

Workers report status changes via JSON messages parsed by `WorkerMessageParser.parse_status()`:

```json
{
  "status": "busy",
  "worker": "hermes-worker-alice",
  "message": "Executing task task-42"
}
```

```python
# skills/hiclaw/worker_registry.py — WorkerRegistry.update_status()
worker = await registry.update_status(
    "hermes-worker-alice",
    status="busy",
    message="Executing task task-42"
)
# worker.status == "busy"
# worker.metadata["last_message"] == "Executing task task-42"
```

## Deregistration

Remove a worker from the registry when it shuts down or is replaced:

```python
# skills/hiclaw/worker_registry.py — WorkerRegistry.remove_worker()
removed = await registry.remove_worker("hermes-worker-alice")
# removed == True
```

## Querying Workers

```python
# List all workers
all_workers = await registry.list_workers()

# List only workers with a specific status
ready_workers = await registry.list_workers(status="ready")

# Get a specific worker
worker = await registry.get_worker("hermes-worker-alice")
```

## Manager-Side: Parsing Worker Messages

```python
# skills/hiclaw/manager_handler.py — WorkerMessageParser
from skills.hiclaw.manager_handler import WorkerMessageParser

# Check if a Matrix message is a worker message
is_worker = WorkerMessageParser.is_worker_message(content)

# Parse registration
reg_data = WorkerMessageParser.parse_registration(content)

# Parse status update
status_tuple = WorkerMessageParser.parse_status(content)
# Returns (status, worker_name, message) or None
```

## Manager-Side: Heartbeat Monitoring

The Manager runs a background task that checks all workers every 5 minutes. Workers that have not sent a heartbeat within the expected window are marked `offline`:

```python
# Pseudocode — Manager checks last_seen_at vs now
for worker in await registry.list_workers():
    last_seen = datetime.fromisoformat(worker.last_seen_at.rstrip("Z"))
    if (datetime.utcnow() - last_seen) > timedelta(minutes=5):
        await registry.update_status(worker.id, "offline")
```

## Worker Container Naming

Workers use container names prefixed with `hermes-worker-` to avoid collision with any `hiclaw-worker-*` containers:

```
hermes-worker-alice
hermes-worker-bob
```

The Manager container is named `hermes-manager`.
