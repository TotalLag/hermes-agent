---
name: hiclaw-task-protocol
description: Natural language task protocol for Manager-to-Worker communication via Matrix DMs. Tasks are sent as natural language with //task-assign prefix and task specification. Workers reply with //task-result prefix and the result. No JSON task protocol — all task content is natural language.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [HiClaw, Task, Protocol, Natural Language, Matrix, DM]
---

# Task Protocol — Natural Language via Matrix DM

The HiClaw task protocol uses **natural language** Matrix direct messages between Manager and Workers. There is no JSON task schema — the Manager sends human-readable task descriptions, and workers respond with human-readable results. Markers are used only as command prefixes to signal intent.

## Message Markers

| Marker | Direction | Purpose |
|--------|-----------|---------|
| `//task-assign` | Manager → Worker | Assign a new task |
| `//task-result` | Worker → Manager | Report task completion |
| `//heartbeat` | Worker → Manager | Worker heartbeat (status ping) |

## Task Assignment (Manager → Worker)

The Manager sends a Matrix DM to the worker with `//task-assign` followed by natural language task description:

```
//task-assign

Task ID: task-001
Spec file: /tasks/spec-001.md

Please analyze the code in /project/src/ and identify all functions that lack type annotations. Write your findings to /tasks/results-001.md in the following format:

## Functions Missing Type Annotations

For each function, include:
- File path and line number
- Function name
- Parameters without annotations

Be thorough and check all .py files recursively.
```

## Task Result (Worker → Manager)

The worker executes the task and replies with `//task-result` followed by natural language result:

```
//task-result

Task ID: task-001
Status: completed

## Functions Missing Type Annotations

### /project/src/auth.py

**Line 14: `def authenticate_user(username, password)`**
- Parameters `username` and `password` lack type annotations

**Line 47: `def create_session(user_id)`**
- Parameter `user_id` lacks type annotation
- Return type not annotated

### /project/src/models/user.py

**Line 8: `class User(Base):`**
- No issues found — all methods are annotated

### Summary

Found 3 functions lacking type annotations across 2 files. Full details in /tasks/results-001.md.
```

## Task Failure (Worker → Manager)

If a task fails, the worker reports with an error description:

```
//task-result

Task ID: task-001
Status: failed

The specified path /project/src/ does not exist. The task cannot be completed because the working directory is inaccessible. Please verify the path and resubmit the task.
```

## Heartbeat (Worker → Manager)

Workers send periodic heartbeats to confirm they are alive:

```
//heartbeat

Worker: hermes-worker-alice
Status: ready
Last completed task: task-000
```

Or when busy:

```
//heartbeat

Worker: hermes-worker-alice
Status: busy
Current task: task-003
Progress: 60%
```

## Protocol Rules

1. **Marker is always first** — `//task-assign`, `//task-result`, or `//heartbeat` must be the first line (or first content after whitespace stripping)
2. **Natural language body** — the content after the marker is plain human-readable text
3. **Task ID always included** — every `//task-assign` and `//task-result` must contain a `Task ID:` field
4. **Status on results** — every `//task-result` must include `Status: completed` or `Status: failed`
5. **No JSON payloads** — the protocol deliberately avoids JSON task schemas; LLMs read and write natural language
6. **Multiline allowed** — task specs and results can span multiple messages/paragraphs

## Shell Examples

### Manager: Send a task

```bash
# The Manager sends this as a Matrix DM to the worker room
echo "//task-assign

Task ID: task-001
Spec file: /tasks/spec-001.md

Please analyze the code in /project/src/ and identify all functions that lack type annotations. Write your findings to /tasks/results-001.md." | send_matrix_dm "hermes-worker-alice"
```

### Worker: Parse incoming task

```python
# Worker receives Matrix DM, extracts task content
message_body = raw_matrix_message.strip()

if message_body.startswith("//task-assign"):
    task_content = message_body[len("//task-assign"):].strip()
    # Parse task_content for Task ID and spec path...
    # Execute the natural language task...
```

### Worker: Send result

```python
result = f"""//task-result

Task ID: {task_id}
Status: completed

{task_result_text}"""

send_matrix_dm("hermes-manager", result)
```

## No JSON Task Protocol

The HiClaw protocol intentionally uses **natural language** instead of structured JSON. This means:

- LLMs can read task specs directly without JSON parsing
- Task specs are human-editable without schema validation
- The protocol is robust to future field additions
- Workers and Manager use the same LLM to parse/generate text

**Do NOT add a JSON task protocol** — the natural language Matrix DM approach is the defined protocol.
