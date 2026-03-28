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

Workers self-register by sending a JSON payload to the Manager Matrix room. The Manager calls `wr_register` to record the worker:

```python
# Call wr_register MCP tool
result = await mcp_tool("wr_register", {
    "worker_id": "hermes-worker-alice",
    "name": "alice",
    "capabilities": ["coding", "research", "file-ops"],
    "version": "1.0.0",
    "matrix_user_id": "@alice:matrix.example.com",
    "device_id": "DEVICEABC123",
})
# result["worker"]["status"] == "registered"
# result["worker"]["registered_at"] is set automatically
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
# Call wr_heartbeat MCP tool
result = await mcp_tool("wr_heartbeat", {
    "worker_id": "hermes-worker-alice",
})
# result["worker"]["last_seen_at"] refreshed; status unchanged
```

The heartbeat flow:
1. Worker sends `//heartbeat` message (or JSON status ping) to Manager room
2. Manager calls `wr_heartbeat(worker_id)`
3. `last_seen_at` is updated to current UTC time
4. Worker status remains whatever it was (`ready`, `busy`, etc.)

## Status Updates

Workers report status changes via JSON messages. The Manager calls `wr_update_status`:

```json
{
  "status": "busy",
  "worker": "hermes-worker-alice",
  "message": "Executing task task-42"
}
```

```python
# Call wr_update_status MCP tool
result = await mcp_tool("wr_update_status", {
    "worker_id": "hermes-worker-alice",
    "status": "busy",
    "message": "Executing task task-42",
})
# result["worker"]["status"] == "busy"
# result["worker"]["metadata"]["last_message"] == "Executing task task-42"
```

## Deregistration

Remove a worker from the registry when it shuts down or is replaced:

```python
# Call wr_remove MCP tool
result = await mcp_tool("wr_remove", {
    "worker_id": "hermes-worker-alice",
})
# result["removed"] == True
```

## Querying Workers

```python
# List all workers (wr_list)
result = await mcp_tool("wr_list", {})
# result["workers"] is a list of all registered workers

# List workers by status (wr_list with status filter)
result = await mcp_tool("wr_list", {"status": "ready"})
# result["workers"] contains only ready workers

# Get a specific worker (wr_get)
result = await mcp_tool("wr_get", {"worker_id": "hermes-worker-alice"})
# result["worker"] contains the worker object, or None if not found
```

## Manager-Side: Parsing Worker Messages

Worker message parsing is handled automatically by `HiClawManagerHandler` (`gateway/hiclaw/manager_handler.py`) — it intercepts worker messages before they reach the LLM agent. The Manager uses MCP tools to interact with worker data after parsing:

```python
# Worker message indicators (for custom routing — normally handled automatically):
# - JSON with "status" + "worker" keys → status update
# - JSON with "id" + "capabilities" + "status" → registration

# Query registered workers (wr_list)
result = await mcp_tool("wr_list", {"status": "ready"})

# Get a specific worker (wr_get)
result = await mcp_tool("wr_get", {"worker_id": "hermes-worker-alice"})
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
