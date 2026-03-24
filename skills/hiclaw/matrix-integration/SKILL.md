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

`WorkerMessageParser` (`skills/hiclaw/manager_handler.py`) parses JSON messages from workers:

```python
from skills.hiclaw.manager_handler import WorkerMessageParser

# Check if a Matrix message is a worker message
is_worker = WorkerMessageParser.is_worker_message(content)
# Returns True if the message contains worker indicators like
# "status" + "worker" keys, or "id" + "capabilities" + "status"

# Parse worker registration
reg_data = WorkerMessageParser.parse_registration(content)
# Returns dict with worker info if all required fields present
# Required: id, name, capabilities, status, version, matrix_user_id, device_id

# Parse status update
status_tuple = WorkerMessageParser.parse_status(content)
# Returns (status, worker_name, message) tuple or None
```

## Incoming Message Flow (Manager Side)

```python
# Pseudocode — Matrix event handler
async def on_matrix_message(room_id: str, sender: str, content: str):
    # Only process messages in the Manager room
    if room_id != MANAGER_ROOM_ID:
        return

    # Check if this is a worker message
    if WorkerMessageParser.is_worker_message(content):
        # Try registration first
        reg_data = WorkerMessageParser.parse_registration(content)
        if reg_data:
            from skills.hiclaw.worker_registry import WorkerRegistry
            registry = WorkerRegistry()
            await registry.register_worker(**reg_data)
            return

        # Try status update
        status_tuple = WorkerMessageParser.parse_status(content)
        if status_tuple:
            status, worker_name, message = status_tuple
            from skills.hiclaw.worker_registry import WorkerRegistry
            registry = WorkerRegistry()
            await registry.update_status(worker_name, status, message)
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
