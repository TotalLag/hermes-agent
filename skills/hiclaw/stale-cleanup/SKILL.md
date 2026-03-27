---
name: hiclaw-stale-cleanup
description: Periodic cleanup of stale workers and tasks for HiClaw. Runs as a scheduled cron job to mark offline workers and fail orphaned tasks. Call wr_get_stale_workers() and tq_fail_stale_tasks() MCP tools to perform cleanup.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [HiClaw, Worker, Task, Cleanup, Cron, Stale, Registry, Queue]
---

# Stale Cleanup — Periodic Worker and Task Cleanup

Performs periodic cleanup of stale workers and tasks for the HiClaw system. This skill is designed to run as a scheduled cron job (typically every 5 minutes) to ensure workers that have died or become unresponsive are marked offline, and orphaned tasks are marked as failed.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HICLAW_STALE_CLEANUP_ENABLED` | `true` | Enable or disable stale cleanup |
| `HICLAW_STALE_CLEANUP_INTERVAL` | `300` | Cleanup interval in seconds (default: 5 minutes) |
| `HICLAW_STALE_THRESHOLD_SECONDS` | `300` | Seconds since last heartbeat to consider a worker stale (default: 5 minutes) |

## Cleanup Actions

This skill performs two cleanup actions in order:

### 1. Mark Stale Workers Offline

Uses `wr_get_stale_workers()` from the worker-registry MCP server to:
- Find workers that have not sent a heartbeat within the threshold period
- Mark them as `offline` in the worker registry
- Return the list of workers that were marked offline

```python
# Call wr_get_stale_workers MCP tool
result = await mcp_tool("wr_get_stale_workers", {
    "timeout_seconds": int(os.getenv("HICLAW_STALE_THRESHOLD_SECONDS", 300)),
})
# result["count"] == number of stale workers marked offline
# result["workers"] == list of stale worker objects
```

### 2. Fail Stale Tasks

Uses `tq_fail_stale_tasks()` from the task-queue MCP server to:
- Find running tasks that have not been updated within the threshold period
- Mark them as `failed` with error "Task timed out"
- Clear their assigned worker (so workers are not stuck in `busy` state)

```python
# Call tq_fail_stale_tasks MCP tool
result = await mcp_tool("tq_fail_stale_tasks", {
    "timeout_seconds": int(os.getenv("HICLAW_STALE_THRESHOLD_SECONDS", 300)),
})
# result["count"] == number of stale tasks marked failed
# result["tasks"] == list of stale task objects
```

## Idempotency

This cleanup is **idempotent** — running it multiple times is safe:
- Workers already marked `offline` are excluded from subsequent checks
- Tasks already marked `failed` are excluded from subsequent checks
- No data is lost; only stale items are updated

## Error Handling

The skill handles MCP server unavailability gracefully:
- If the worker-registry MCP is unavailable, log a warning and skip worker cleanup
- If the task-queue MCP is unavailable, log a warning and skip task cleanup
- Log all cleanup results at INFO level (how many workers/tasks were cleaned)
- Never block or fail the entire cleanup due to transient errors

## Prometheus Metrics

When Prometheus metrics are enabled, the cleanup updates the following counters:

| Metric | Type | Description |
|--------|------|-------------|
| `hiclaw_stale_workers_cleaned_total` | Counter | Total number of stale workers marked offline |
| `hiclaw_stale_tasks_cleaned_total` | Counter | Total number of stale tasks marked failed |

Metrics are only updated when metrics are enabled and the cleanup actually performs work.

## Cron Setup

To run this cleanup as a cron job, create a scheduled job:

```python
from cron.jobs import create_job

create_job(
    prompt="Run stale cleanup: check for stale workers and tasks, mark them offline/failed.",
    schedule=f"every {int(os.getenv('HICLAW_STALE_CLEANUP_INTERVAL', 300)) // 60}m",
    name="hiclaw-stale-cleanup",
    skills=["hiclaw-stale-cleanup"],
)
```

Or via the Hermes CLI:
```
/cron add "Run stale cleanup" every 5m --skill hiclaw-stale-cleanup
```

## Example Cleanup Run

When the cleanup runs, it performs these steps:

1. **Check if enabled**: Read `HICLAW_STALE_CLEANUP_ENABLED` — if `false`, skip entirely
2. **Get stale workers**: Call `wr_get_stale_workers()` → log how many were marked offline
3. **Fail stale tasks**: Call `tq_fail_stale_tasks()` → log how many were marked failed
4. **Update metrics**: Increment Prometheus counters if enabled
5. **Log summary**: Log a summary like "Stale cleanup: 2 workers offline, 3 tasks failed"

## MCP Server Requirements

This skill requires the following MCP servers to be configured:
- `worker-registry` — provides `wr_get_stale_workers` tool
- `task-queue` — provides `tq_fail_stale_tasks` tool

Both servers must be running and accessible for the cleanup to work. If either is unavailable, the cleanup logs a warning and continues with the available server.

## Timing

| Parameter | Value | Notes |
|-----------|-------|-------|
| Default interval | 5 minutes | Configurable via `HICLAW_STALE_CLEANUP_INTERVAL` |
| Stale threshold | 5 minutes | Configurable via `HICLAW_STALE_THRESHOLD_SECONDS` |
| Heartbeat interval | 2 minutes | Workers send heartbeats every 2 minutes (from worker-lifecycle skill) |

The stale threshold should be at least 2-3x the heartbeat interval to avoid false positives. The default of 5 minutes (2.5x heartbeat) is reasonable, but you may want to increase it if you have network latency or clock skew issues.
