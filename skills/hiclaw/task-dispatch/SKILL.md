---
name: hiclaw-task-dispatch
description: Dispatch tasks from Manager to workers using ManagerState. Covers adding tasks, assigning to a worker, marking complete or failed, and querying task status. Task assignment uses natural language Matrix DMs with //task-assign and //task-result markers (see task-protocol skill).
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [HiClaw, Task, Dispatch, Manager, State]
---

# Task Dispatch — Manager to Worker Task Assignment

The Manager dispatches tasks to workers using `ManagerState` (`skills/hiclaw/manager_state.py`). Tasks move through states: `pending` → `assigned` → `running` → `completed` / `failed`. The Manager sends tasks as natural language Matrix DMs with `//task-assign` prefix; workers reply with `//task-result` prefix.

## Task Lifecycle

```
pending → assigned → running → completed
                          → failed
```

## TaskInfo Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Unique task identifier |
| `spec_path` | str | Path to task specification file |
| `assigned_worker` | str or None | Worker ID assigned to this task |
| `status` | str | One of: pending, assigned, running, completed, failed |
| `created_at` | str | ISO timestamp of creation |
| `updated_at` | str | ISO timestamp of last update |
| `result_path` | str or None | Path to result file (set on completion) |
| `error` | str or None | Error message (set on failure) |

## Adding a Task

```python
# skills/hiclaw/manager_state.py — ManagerState.add_task()
from skills.hiclaw.manager_state import ManagerState

state = ManagerState()
task = await state.add_task(
    task_id="task-001",
    spec_path="/tasks/spec-001.md"
)
# task.status == "pending"
```

## Assigning a Task to a Worker

```python
# skills/hiclaw/manager_state.py — ManagerState.assign_task()
task = await state.assign_task(
    task_id="task-001",
    worker_id="hermes-worker-alice"
)
# task.status == "assigned"
# task.assigned_worker == "hermes-worker-alice"
```

## Completing a Task

```python
# skills/hiclaw/manager_state.py — ManagerState.complete_task()
task = await state.complete_task(
    task_id="task-001",
    result_path="/tasks/result-001.md"
)
# task.status == "completed"
# task.result_path == "/tasks/result-001.md"
```

## Failing a Task

```python
# skills/hiclaw/manager_state.py — ManagerState.fail_task()
task = await state.fail_task(
    task_id="task-001",
    error="Worker crashed during execution"
)
# task.status == "failed"
# task.error == "Worker crashed during execution"
```

## Querying Tasks

```python
# List all tasks
all_tasks = await state.list_tasks()

# List tasks by status
pending = await state.list_tasks(status="pending")
running = await state.list_tasks(status="running")

# Get a specific task
task = await state.get_task("task-001")
```

## Getting Statistics

```python
# skills/hiclaw/manager_state.py — ManagerState.get_stats()
stats = await state.get_stats()
# Returns: {"total_tasks": 10, "completed_tasks": 7, "failed_tasks": 2}
```

## Manager Mode

`ManagerState` tracks the Manager's operating mode via `ManagerMode` enum:

```python
# skills/hiclaw/manager_state.py — ManagerMode
from skills.hiclaw.manager_state import ManagerMode

await state.set_mode(ManagerMode.IDLE)       # No tasks being dispatched
await state.set_mode(ManagerMode.DISPATCHING)  # Actively assigning tasks
await state.set_mode(ManagerMode.MONITORING)   # Monitoring running tasks

mode = await state.get_mode()  # Returns ManagerMode enum value
```

## Dispatch Workflow

1. User or automated system creates a task spec (markdown file describing the work)
2. Manager calls `add_task(task_id, spec_path)` → task is `pending`
3. Manager selects an available worker and calls `assign_task(task_id, worker_id)` → task is `assigned`
4. Manager sends task to worker via Matrix DM with `//task-assign` prefix (see task-protocol skill)
5. Worker begins execution, sends status updates via Matrix
6. When done, worker replies with `//task-result` prefix
7. Manager calls `complete_task()` or `fail_task()` based on result

## State Persistence

All state is persisted to `~/.hermes/hiclaw/state.json`. The file is rewritten on every mutation via `_save()`.
