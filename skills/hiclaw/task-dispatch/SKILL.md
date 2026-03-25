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
# Call tq_add_task MCP tool
result = await mcp_tool("tq_add_task", {
    "task_id": "task-001",
    "spec_path": "/tasks/spec-001.md",
})
# result["task"]["status"] == "pending"
```

## Assigning a Task to a Worker

```python
# Call tq_assign MCP tool
result = await mcp_tool("tq_assign", {
    "task_id": "task-001",
    "worker_id": "hermes-worker-alice",
})
# result["task"]["status"] == "assigned"
# result["task"]["assigned_worker"] == "hermes-worker-alice"
```

## Completing a Task

```python
# Call tq_complete MCP tool
result = await mcp_tool("tq_complete", {
    "task_id": "task-001",
    "result_path": "/tasks/result-001.md",
})
# result["task"]["status"] == "completed"
# result["task"]["result_path"] == "/tasks/result-001.md"
```

## Failing a Task

```python
# Call tq_fail MCP tool
result = await mcp_tool("tq_fail", {
    "task_id": "task-001",
    "error": "Worker crashed during execution",
})
# result["task"]["status"] == "failed"
# result["task"]["error"] == "Worker crashed during execution"
```

## Querying Tasks

```python
# List all tasks (tq_list)
result = await mcp_tool("tq_list", {})
# result["tasks"] is a list of all tasks

# List tasks by status (tq_list with status filter)
result = await mcp_tool("tq_list", {"status": "pending"})
# result["tasks"] contains only pending tasks

# Get a specific task (tq_get)
result = await mcp_tool("tq_get", {"task_id": "task-001"})
# result["task"] contains the task object, or None if not found
```

## Getting Statistics

```python
# Call tq_stats MCP tool
result = await mcp_tool("tq_stats", {})
# Returns: {"total_tasks": 10, "completed_tasks": 7, "failed_tasks": 2}
```

## Manager Mode

The task queue tracks the Manager's operating mode. Use `tq_set_mode` and `tq_get_mode`:

```python
# Set manager mode to IDLE (no tasks being dispatched)
await mcp_tool("tq_set_mode", {"mode": "idle"})

# Set manager mode to DISPATCHING (actively assigning tasks)
await mcp_tool("tq_set_mode", {"mode": "dispatching"})

# Set manager mode to MONITORING (monitoring running tasks)
await mcp_tool("tq_set_mode", {"mode": "monitoring"})

# Get current manager mode
result = await mcp_tool("tq_get_mode", {})
# result["mode"] is "idle", "dispatching", or "monitoring"
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
