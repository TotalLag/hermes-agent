# Heartbeat Monitor — Manager Checklist

Procedures for the hiclaw Manager's heartbeat monitoring loop.

## Purpose

Workers send periodic heartbeats to signal they are alive and able to accept tasks. The Manager must track these heartbeats and alert admin when workers miss them.

## Heartbeat Parameters

| Parameter | Value |
|-----------|-------|
| Expected interval | Every 30 seconds |
| Miss threshold | 3 consecutive missed heartbeats |
| Stale flag | After miss threshold exceeded |
| Deregister grace | Admin command required |

## Monitoring Loop

### Each Heartbeat Received

- [ ] Log heartbeat with worker_id and timestamp
- [ ] Update worker's `last_heartbeat` to current time
- [ ] Reset miss count to 0
- [ ] If worker was previously stale, mark as active and notify admin

### Each Heartbeat Interval (every 30s)

For each registered worker:
- [ ] Calculate seconds since `last_heartbeat`
- [ ] If `since_last_heartbeat > 30s` and `< 90s`: increment miss count, log warning
- [ ] If miss count >= 3: mark worker as stale, notify admin immediately

### On Worker Registration

- [ ] Initialize `last_heartbeat` to current time
- [ ] Initialize `miss_count` to 0
- [ ] Log new worker added to pool
- [ ] Report to admin: "Worker [worker_id] registered"

### On Worker Deregistration

- [ ] Remove worker from active pool
- [ ] Log deregistration event and reason
- [ ] Report to admin: "Worker [worker_id] deregistered"
- [ ] Check if worker had running tasks — alert admin if so

## Alert Messages

### Missed Heartbeat Warning
"Worker [worker_id] missed heartbeat #[N] (last seen [X]s ago)"

### Worker Stale
"Worker [worker_id] is now STALE — [N] missed heartbeats. Recommend admin action."

### Worker Recovered
"Worker [worker_id] is back — heartbeat received after [N] misses."

### Running Task Worker Gone Stale
"WARNING: Worker [worker_id] went stale with [N] task(s) still running. Tasks: [task_ids]. Admin should decide whether to reassign."

## Admin Notification Triggers

Notify admin immediately when:
- [ ] Worker first exceeds miss threshold (becomes stale)
- [ ] Worker had running tasks when it went stale
- [ ] Worker reappears after being stale
- [ ] Worker deregisters unexpectedly
- [ ] MinIO connection fails (results may not be stored)

## Logging Requirements

Log entry format:
```
[timestamp] [event_type] worker_id details
```

Events to log:
- [ ] HEARTBEAT_RECEIVED
- [ ] HEARTBEAT_MISSED
- [ ] WORKER_STALE
- [ ] WORKER_RECOVERED
- [ ] WORKER_REGISTERED
- [ ] WORKER_DEREGISTERED
- [ ] MINIO_ERROR
