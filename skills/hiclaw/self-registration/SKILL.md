---
name: hiclaw-self-registration
description: Worker self-registration on first boot. Workers automatically discover the Manager Matrix room and register themselves by sending their info (id, name, capabilities, version, Matrix user ID, device ID) as a JSON payload. No manual setup required.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [HiClaw, Worker, Self-Registration, Boot, Matrix]
---

# Self-Registration — Worker Boots and Registers Automatically

Workers self-register on first boot. There is **no manual setup** — the worker reads its environment, constructs a registration payload, and sends it to the Manager Matrix room.

## Registration Flow

```
1. Worker container starts (hermes-worker-{name})
2. Worker reads environment variables / container metadata
3. Worker constructs JSON registration payload
4. Worker sends DM to Manager room
5. Manager parses payload with WorkerMessageParser.parse_registration()
6. Manager calls WorkerRegistry.register_worker()
7. Worker is now tracked and can receive tasks
```

## Registration Payload

Workers send this JSON payload to the Manager on boot:

```json
{
  "id": "hermes-worker-alice",
  "name": "alice",
  "capabilities": ["coding", "research", "file-ops", "web-search"],
  "status": "registered",
  "version": "1.0.0",
  "matrix_user_id": "@alice:matrix.example.com",
  "device_id": "DEVICEABC123"
}
```

## Worker-Side Registration Code

```python
# Worker sends registration on boot
import json
import os

WORKER_ID = os.environ.get("HERMES_WORKER_ID", "hermes-worker-unknown")
WORKER_NAME = os.environ.get("HERMES_WORKER_NAME", "unknown")
WORKER_CAPABILITIES = os.environ.get("HERMES_WORKER_CAPABILITIES", "").split(",")
WORKER_VERSION = os.environ.get("HERMES_WORKER_VERSION", "1.0.0")
MATRIX_USER_ID = os.environ.get("MATRIX_USER_ID", "")
DEVICE_ID = os.environ.get("DEVICE_ID", "unknown")

registration_payload = {
    "id": WORKER_ID,
    "name": WORKER_NAME,
    "capabilities": [c for c in WORKER_CAPABILITIES if c],
    "status": "registered",
    "version": WORKER_VERSION,
    "matrix_user_id": MATRIX_USER_ID,
    "device_id": DEVICE_ID,
}

# Send to Manager Matrix room
await matrix_client.send_dm(
    MANAGER_MATRIX_USER_ID,
    json.dumps(registration_payload)
)
```

## Environment Variables for Workers

| Variable | Description | Example |
|----------|-------------|---------|
| `HERMES_WORKER_ID` | Unique worker identifier | `hermes-worker-alice` |
| `HERMES_WORKER_NAME` | Human-readable name | `alice` |
| `HERMES_WORKER_CAPABILITIES` | Comma-separated capability list | `coding,research,file-ops` |
| `HERMES_WORKER_VERSION` | Worker software version | `1.0.0` |
| `MATRIX_USER_ID` | Worker's Matrix user ID | `@alice:matrix.example.com` |
| `DEVICE_ID` | Unique device/session ID | `DEVICEABC123` |
| `MANAGER_MATRIX_USER_ID` | Manager's Matrix user ID | `@hermes-manager:matrix.example.com` |
| `MANAGER_ROOM_ID` | Manager's Matrix room ID | `!manager-room:matrix.example.com` |

## Manager-Side Registration Handler

When the Manager receives a worker registration message, it calls the `wr_register` MCP tool to record the worker in the registry:

```python
# Call wr_register MCP tool — HiClawManagerHandler parses the message
# and calls the tool on your behalf, or you can call it directly:
result = await mcp_tool("wr_register", {
    "worker_id": "hermes-worker-alice",
    "name": "alice",
    "capabilities": ["coding", "research", "file-ops"],
    "version": "1.0.0",
    "matrix_user_id": "@alice:matrix.example.com",
    "device_id": "DEVICEABC123",
})
# result["worker"] contains the registered worker object
# result["worker"]["status"] == "registered"
# result["worker"]["registered_at"] is set automatically
```

To update a worker's status after registration:

```python
# Call wr_update_status MCP tool
result = await mcp_tool("wr_update_status", {
    "worker_id": "hermes-worker-alice",
    "status": "ready",
    "message": "Worker initialized and ready",
})
```

## Container Naming Convention

Workers use `hermes-worker-{name}` container naming to avoid collision with any `hiclaw-worker-*` containers:

```bash
# Worker containers
docker run --name hermes-worker-alice ...
docker run --name hermes-worker-bob ...

# Manager container
docker run --name hermes-manager ...
```

## First Boot Only

Registration happens **only on first boot**. On subsequent starts, if the worker is already in the registry, the Manager can either:

1. **Re-register** — update `last_seen_at` and refresh worker info
2. **Skip** — worker sends a heartbeat instead, Manager updates `last_seen_at`

```python
# Check if worker already registered before registering
existing = await registry.get_worker(WORKER_ID)
if existing:
    # Worker already known — just refresh heartbeat
    await registry.heartbeat(WORKER_ID)
else:
    # New worker — full registration
    await registry.register_worker(...)
```

## Capability Discovery

Workers advertise capabilities at registration. The Manager uses these to select the right worker for a task:

```python
# Manager selects worker by capability
async def select_worker_for_task(required_capability: str) -> str:
    registry = WorkerRegistry()
    for worker in await registry.list_workers(status="ready"):
        if required_capability in worker.capabilities:
            return worker.id
    return None  # No worker with required capability
```

## Post-Registration: Ready Status

After registering, the worker sets its status to `ready` so the Manager knows it can receive tasks:

```python
# Worker sends status update after registration
await matrix_client.send_dm(
    MANAGER_MATRIX_USER_ID,
    json.dumps({
        "status": "ready",
        "worker": WORKER_ID,
        "message": "Worker initialized and ready"
    })
)
```

## Timing

- Worker sends registration payload immediately on boot
- Manager parses and registers worker synchronously (incoming message handler)
- Worker sends `ready` status update immediately after successful registration acknowledgment
