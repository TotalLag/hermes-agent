---
name: hiclaw-matrix-integration
description: Matrix DM handling for Hermes Manager-to-Worker communications. Workers and Manager communicate via Matrix direct messages in a shared Manager room. Covers message parsing with WorkerMessageParser, sending DMs, and handling the Matrix event loop.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [HiClaw, Matrix, DM, Integration, Worker, Manager]
---

# Matrix Integration — Manager-Worker DM Communication

Workers and the Manager communicate exclusively via Matrix direct messages. Workers join the Manager's Matrix room and send/receive messages there. The Manager parses incoming messages using `WorkerMessageParser` (`skills/hiclaw/manager_handler.py`).

## Architecture

```
Worker sends Matrix DM
        │
        ▼
┌───────────────────────┐
│  Manager Matrix Room  │
│  (!manager-room:...)  │
└───────────────────────┘
        │
        ▼
  Manager receives
  parses with
  WorkerMessageParser
        │
        ▼
  Updates WorkerRegistry
  and ManagerState
```

## WorkerMessageParser

`WorkerMessageParser` (`gateway/hiclaw/protocol.py`) parses JSON messages from workers. The `HiClawManagerHandler` intercepts worker messages automatically — parsing is handled internally. The Manager uses these MCP tools to interact with worker data after parsing:

```python
# List all registered workers (wr_list)
result = await mcp_tool("wr_list", {"status": "ready"})
# Returns list of workers matching the status filter

# Get a specific worker by ID (wr_get)
result = await mcp_tool("wr_get", {"worker_id": "hermes-worker-alice"})
# Returns worker object or None

# Check if content looks like a worker message (for custom routing)
# This is handled internally by HiClawManagerHandler — you don't call it directly.
# Worker message indicators: "status" + "worker" keys, or "id" + "capabilities" + "status"
```

## Incoming Message Flow (Manager Side)

```python
# Pseudocode — Matrix event handler
async def on_matrix_message(room_id: str, sender: str, content: str):
    # Only process messages in the Manager room
    if room_id != MANAGER_ROOM_ID:
        return

    # Check if this is a worker message (handled automatically by HiClawManagerHandler)
    # Worker message indicators: "status" + "worker" keys, or "id" + "capabilities" + "status"

    # When HiClawManagerHandler parses a registration, it calls wr_register:
    if is_worker_registration_content(content):
        reg_data = parse_registration(content)  # handled internally
        # Manager calls MCP tool to register worker:
        result = await mcp_tool("wr_register", {
            "worker_id": reg_data["id"],
            "name": reg_data["name"],
            "capabilities": reg_data["capabilities"],
            "version": reg_data["version"],
            "matrix_user_id": reg_data["matrix_user_id"],
            "device_id": reg_data["device_id"],
        })
        return

    # When HiClawManagerHandler parses a status update, it calls wr_update_status:
    if is_worker_status_content(content):
        status, worker_name, message = parse_status(content)  # handled internally
        result = await mcp_tool("wr_update_status", {
            "worker_id": worker_name,
            "status": status,
            "message": message,
        })
        return
```

## Sending Messages (Manager Side)

```python
# Send a task to a worker via Matrix DM
TASK_TEMPLATE = """//task-assign

Task ID: {task_id}
Spec file: {spec_path}

{task_description}"""

message = TASK_TEMPLATE.format(
    task_id=task.id,
    spec_path=task.spec_path,
    task_description=task_description
)
await matrix_client.send_dm(worker.matrix_user_id, message)
```

## Worker-Side: Receiving Messages

```python
# Worker Matrix event handler
async def on_matrix_message(room_id: str, sender: str, content: str):
    content = content.strip()

    if content.startswith("//task-assign"):
        task_body = content[len("//task-assign"):].strip()
        # Parse Task ID from task_body
        task_id = extract_task_id(task_body)
        # Execute the task...
        await execute_task(task_id, task_body)
        return

    elif content.startswith("//heartbeat"):
        # Respond with heartbeat ack
        await matrix_client.send_dm(
            MANAGER_USER_ID,
            f"//heartbeat\nWorker: {WORKER_NAME}\nStatus: {CURRENT_STATUS}"
        )
        return
```

## Worker-Side: Sending Results

```python
# After completing a task
RESULT_TEMPLATE = """//task-result

Task ID: {task_id}
Status: {status}

{result_text}"""

result_message = RESULT_TEMPLATE.format(
    task_id=task_id,
    status="completed",
    result_text=task_result
)
await matrix_client.send_dm(MANAGER_USER_ID, result_message)
```

## Matrix Room Setup

The Manager creates and owns the shared Matrix room:

```
Room name: hermes-manager-workers
Room ID: !manager-room:matrix.example.com
```

Workers join this room on registration. The Manager sends tasks to the room (broadcast) or via individual DMs to specific workers.

## Key Matrix User IDs

| Component | Matrix User ID | Container Name |
|-----------|---------------|---------------|
| Manager | `@hermes-manager:matrix.example.com` | `hermes-manager` |
| Worker Alice | `@hermes-worker-alice:matrix.example.com` | `hermes-worker-alice` |
| Worker Bob | `@hermes-worker-bob:matrix.example.com` | `hermes-worker-bob` |

## Message Types Summary

| Content Type | Sender | Parsed By |
|-------------|--------|-----------|
| Registration JSON | Worker | `WorkerMessageParser.parse_registration()` |
| Status JSON | Worker | `WorkerMessageParser.parse_status()` |
| `//task-assign` DM | Manager | Worker (natural language) |
| `//task-result` DM | Worker | Manager (natural language) |
| `//heartbeat` DM | Worker | Manager (natural language) |
