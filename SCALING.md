# HiClaw Scaling Guide

This guide covers capacity planning, scaling triggers, and operational limits for HiClaw production deployments.

---

## Resource Planning

### Per-Node Requirements

| Component | CPU | RAM | Disk |
|-----------|-----|-----|------|
| Manager node | 2 cores | 2 GB | 10 GB |
| Worker container | 1 core | 512 MB | ephemeral |

### Manager-to-Worker Ratio

A single manager can coordinate:
- **Recommended maximum**: 10 active workers
- **Tested maximum**: 50 concurrent workers (under controlled conditions)

Adding more than 10 workers per manager increases the probability of Matrix message storms during bulk task assignment. If you need more than 10 workers, deploy multiple manager instances with separate Matrix homeservers.

### Task Throughput

Each worker processes tasks sequentially. Throughput depends on task complexity and LLM response time.

| Task Type | Typical Duration | Tasks/Hour/Worker |
|-----------|-----------------|-------------------|
| Simple tool call | 5-15 seconds | 240-720 |
| Multi-step agentic | 1-5 minutes | 12-60 |
| Complex research | 5-15 minutes | 4-12 |

---

## Scaling Triggers

Monitor these metrics and scale when thresholds are consistently breached.

### Worker Utilization

Trigger scale-up when:
- `workers_ready` / `workers_total` ratio < 60% for > 5 minutes
- Average queue depth > 20 tasks

Trigger scale-down when:
- `workers_ready` / `workers_total` ratio > 90% for > 15 minutes
- Queue depth < 5 for > 15 minutes

### Task Latency

Trigger investigation when:
- Median task completion time > 60 seconds (above your SLO)
- P95 task completion time > 300 seconds

### Memory Pressure

If manager OOMs, add 1 GB RAM and investigate:
- Session store growth rate
- Unacked message backlog
- Worker container memory leaks

---

## Scaling Operations

### Scale Worker Count

```bash
# Scale up
docker service scale hiclaw-worker=20

# Scale down (graceful — waits for in-flight tasks)
docker service scale hiclaw-worker=5
```

### Add Manager Node

For high availability, add a manager to the Swarm:

```bash
# On the new manager node
docker swarm join-token manager
# Copy the join command output and run it on the new manager

# Verify manager count
docker node ls
```

The manager选举 is automatic — no manual reconfiguration of existing services required.

---

## Operational Limits

### Known Boundaries

| Limit | Value | Notes |
|-------|-------|-------|
| Max workers per manager | 50 (tested) | Beyond 10, Matrix message volume increases |
| Max task message size | 64 KB | Enforced by Synapse |
| Max sessions per manager | ~10,000 | Depends on session turnover rate |
| Session retention | 30 days default | Configurable via session TTL |
| Task queue depth | Unlimited | Backpressure handled via `tasks_pending` metric |

### What Happens at Limits

- **Worker limit exceeded**: New tasks queue locally on the manager. No error returned to users until queue depth > 1000.
- **Session limit exceeded**: Oldest inactive sessions are evicted. Active sessions are never evicted.
- **Task queue overflow**: Manager logs `queue_depth_threshold_exceeded` warning. Tasks continue to queue.

---

## Key Metrics to Monitor

The following Prometheus metrics are exposed at `http://localhost:9090/metrics` (configurable via `HICLAW_METRICS_PORT`).

| Metric | Description | Scaling Relevance |
|--------|-------------|-----------------|
| `hiclaw_workers_ready` | Workers currently able to accept tasks | Primary scale trigger |
| `hiclaw_workers_total` | All registered workers (including stale) | Detects registration storms |
| `hiclaw_tasks_pending` | Tasks waiting for a worker | Queue depth monitoring |
| `hiclaw_tasks_completed` | Tasks finished successfully | Throughput measurement |
| `hiclaw_tasks_failed` | Tasks that errored | Health indicator |
| `hiclaw_heartbeat_latency_seconds` | Time to receive worker heartbeat | Network/load indicator |

### Recommended Alert Thresholds

```yaml
alerts:
  - name: WorkerUtilizationLow
    expr: hiclaw_workers_ready / hiclaw_workers_total < 0.6
    duration: 5m
    severity: warning

  - name: TaskQueueBacklog
    expr: hiclaw_tasks_pending > 20
    duration: 5m
    severity: warning

  - name: HighTaskLatency
    expr: histogram_quantile(0.95, rate(hiclaw_task_duration_seconds_bucket[5m])) > 300
    duration: 10m
    severity: warning
```

---

## Horizontal vs Vertical Scaling

### Horizontal (Add Workers)

Best for:
- I/O-bound tasks (most LLM workloads)
- Variable or unpredictable load
- Fault tolerance requirements

```bash
docker service scale hiclaw-worker=N
```

### Vertical (Larger Manager)

Consider when:
- Session count exceeds 5,000
- Matrix sync latency > 2 seconds
- Manager CPU consistently > 80%

Increase manager RAM via `HICLAW_MANAGER_MEMORY_LIMIT` in the deploy compose file, then redeploy.

---

## Load Shedding

If the system is overwhelmed, HiClaw shed loads by:

1. **Rejecting new tasks** when `tasks_pending > 1000` — manager returns 503 to Matrix senders
2. **Pausing Matrix sync** when `workers_ready == 0` for > 60 seconds — prevents message storm accumulation
3. **Closing inactive sessions** when session store exceeds 1 GB — evicts sessions with no activity in 7 days

These thresholds are not user-configurable. If you need different behavior, the relevant code is in `gateway/hiclaw/manager_handler.py`.
