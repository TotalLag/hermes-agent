# HiClaw Troubleshooting Guide

This guide covers common failures and debugging procedures for HiClaw deployments.

---

## Common Failures

### 1. Worker Fails to Register — "Worker already registered" Error

**Symptom:**
Worker container starts but never becomes ready. Manager logs show "Worker already registered" messages repeatedly.

**Root Cause:**
The worker is attempting to register with the same worker_id as an existing entry in the worker registry. This typically happens when:
- A previous worker container was not properly cleaned up
- The worker is restarting with the same name

**Diagnosis:**
```bash
# Check manager logs for registration messages
docker service logs hiclaw_hermes-manager --grep "already registered" --tail 20

# Check current worker states in registry
docker exec hiclaw_hermes-manager python3 -c "
import sqlite3
conn = sqlite3.connect('/root/.hermes/hiclaw/workers-registry.db')
for row in conn.execute('SELECT worker_id, name, status, last_heartbeat FROM workers'):
    print(row)
"
```

**Resolution:**
```bash
# Option 1: Remove stale worker from registry
docker exec hiclaw_hermes-manager python3 -c "
import sqlite3
conn = sqlite3.connect('/root/.hermes/hiclaw/workers-registry.db')
conn.execute('DELETE FROM workers WHERE name = \"worker-1\"')
conn.commit()
print('Removed stale worker-1')
"

# Option 2: Force remove the worker container and redeploy
docker service scale hiclaw_hermes-worker-1=0
docker service scale hiclaw_hermes-worker-1=1
```

**Prevention:**
- Ensure `HICLAW_STALE_CLEANUP_ENABLED=true` is set
- Set `HICLAW_STALE_THRESHOLD_SECONDS=300` appropriately for your environment

---

### 2. Manager Can't Create Workers — Docker Socket Permission Denied

**Symptom:**
`hiclaw_create_worker` tool fails with "permission denied" or "Docker socket not accessible" errors.

**Root Cause:**
The manager container does not have access to the Docker socket, or the socket permissions are incorrect.

**Diagnosis:**
```bash
# Check if Docker socket is mounted
docker inspect hiclaw_hermes-manager --format='{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'

# Test Docker socket accessibility from inside container
docker exec hiclaw_hermes-manager docker ps

# Check socket permissions on host
ls -la /var/run/docker.sock
```

**Resolution:**
```bash
# Ensure socket is mounted read-only in docker-compose
# Add to hermes-manager service:
# volumes:
#   - /var/run/docker.sock:/var/run/docker.sock:ro

# Restart the service
docker service update --force hiclaw_hermes-manager
```

**Prevention:**
- Always mount Docker socket as read-only (`:ro`) for security
- Verify socket permissions are `666` or owner is `root:docker`

---

### 3. Tasks Stuck in "assigned" State — Stale Worker Not Clean Up

**Symptom:**
Tasks remain in "assigned" state indefinitely. Workers appear idle but no work is dispatched.

**Root Cause:**
A worker that was assigned a task has gone offline without completing it. The stale cleanup is either disabled or not running frequently enough.

**Diagnosis:**
```bash
# Check for stale workers
docker exec hiclaw_hermes-manager python3 -c "
import sqlite3
from datetime import datetime, timedelta
conn = sqlite3.connect('/root/.hermes/hiclaw/workers-registry.db')
threshold = (datetime.now() - timedelta(seconds=300)).isoformat()
for row in conn.execute('SELECT worker_id, name, status, last_heartbeat FROM workers WHERE last_heartbeat < ?', (threshold,)):
    print('STALE:', row)
"

# Check for tasks in assigned state
docker exec hiclaw_hermes-manager python3 -c "
import sqlite3
conn = sqlite3.connect('/root/.hermes/hiclaw/task-queue.db')
for row in conn.execute('SELECT task_id, status, assigned_worker, updated_at FROM tasks WHERE status = \"assigned\"'):
    print(row)
"
```

**Resolution:**
```bash
# Run stale cleanup manually
docker exec hiclaw_hermes-manager python3 -c "
import sys
sys.path.insert(0, '/app')
from mcp_servers.worker_registry.server import wr_get_stale_workers
result = wr_get_stale_workers(timeout_seconds=300)
print(result)
"

# Force-fail stale tasks
docker exec hiclaw_hermes-manager python3 -c "
import sys
sys.path.insert(0, '/app')
from mcp_servers.task_queue.server import tq_fail_stale_tasks
result = tq_fail_stale_tasks(timeout_seconds=300)
print(result)
"
```

**Prevention:**
- Enable stale cleanup: `HICLAW_STALE_CLEANUP_ENABLED=true`
- Set appropriate interval: `HICLAW_STALE_CLEANUP_INTERVAL=300` (5 minutes)
- Set appropriate threshold: `HICLAW_STALE_THRESHOLD_SECONDS=300`

---

### 4. Matrix DM Not Delivered — is_hiclaw_message Bypass Not Working

**Symptom:**
Worker sends a message but the manager doesn't process it. Task results never arrive.

**Root Cause:**
The `HiClawManagerHandler.is_hiclaw_message()` method is not correctly identifying worker messages, or the Matrix adapter is not routing them properly.

**Diagnosis:**
```bash
# Check for unrecognized worker payloads in manager logs
docker service logs hiclaw_hermes-manager --grep "unroutable" --tail 30

# Enable debug logging to see message parsing
docker service update \
  --env HICLAW_LOG_LEVEL=DEBUG \
  hiclaw_hermes-manager

# Watch for message parsing in real-time
docker service logs -f hiclaw_hermes-manager --grep "is_worker_message\|parse"
```

**Resolution:**
```bash
# Restart the manager to pick up configuration changes
docker service update --force hiclaw_hermes-manager

# Verify the protocol parser is working
docker exec hiclaw_hermes-manager python3 -c "
from gateway.hiclaw.protocol import WorkerMessageParser
test_msg = '{\"type\": \"registration\", \"id\": \"test-worker\", \"name\": \"test\"}'
print('is_worker_message:', WorkerMessageParser.is_worker_message(test_msg))
"
```

**Prevention:**
- Ensure worker is using correct message format per `gateway/hiclaw/protocol.py`
- Verify Matrix room ID matches between manager and worker config

---

### 5. Circuit Breaker Open — Docker Daemon Unresponsive

**Symptom:**
New workers cannot be created. Logs show "Circuit breaker open" errors.

**Root Cause:**
The Docker circuit breaker in `gateway/hiclaw/circuit_breaker.py` has opened after too many Docker operation failures. This protects against cascading failures when Docker is overwhelmed.

**Diagnosis:**
```bash
# Check circuit breaker state via health endpoint
docker exec hiclaw_hermes-manager curl http://localhost:8080/health/ready

# Check for circuit breaker logs
docker service logs hiclaw_hermes-manager --grep "Circuit breaker" --tail 30

# Test Docker daemon responsiveness
docker exec hiclaw_hermes-manager docker info
```

**Resolution:**
```bash
# Option 1: Wait for automatic recovery (default 5 minutes)
# The circuit breaker transitions to HALF_OPEN after cooldown_timeout

# Option 2: Force reset by restarting manager
docker service update --force hiclaw_hermes-manager

# Option 3: Adjust circuit breaker thresholds if Docker is legitimately slow
# Set in hiclaw.env:
# HICLAW_DOCKER_CIRCUIT_BREAKER_THRESHOLD=5  # More failures allowed
# HICLAW_DOCKER_CIRCUIT_BREAKER_TIMEOUT=60    # Faster recovery
```

**Prevention:**
- Monitor Docker daemon health: `docker system df` and `docker stats`
- Set appropriate circuit breaker thresholds for your Docker performance
- Ensure adequate system resources (RAM, CPU) for Docker operations

---

## Log Patterns

Search these patterns in service logs to diagnose issues:

### Critical Patterns

| Pattern | Meaning | Action |
|---------|---------|--------|
| `Circuit breaker.*OPEN` | Docker operations suspended | Check Docker daemon health |
| `Circuit breaker.*transitioning.*OPEN` | Circuit just opened | Review Docker error logs |
| `stale worker detected` | Worker missed heartbeats | Check worker container health |
| `task timeout` | Task exceeded max runtime | Increase timeout or check worker |
| `MCP connection refused` | MCP server not reachable | Check MCP server status |

### Warning Patterns

| Pattern | Meaning | Action |
|---------|---------|--------|
| `heartbeat missed` | Worker missed one heartbeat | Monitor, may recover |
| `retrying.*attempt \d+` | Reconnection in progress | Wait for recovery |
| `Docker socket unavailable` | Docker access issue | Check socket mount |
| `registration sender mismatch` | Worker ID mismatch | Verify worker configuration |

### Info Patterns

| Pattern | Meaning |
|---------|---------|
| `worker.*registered` | New worker successfully registered |
| `heartbeat received` | Normal heartbeat received |
| `task.*assigned` | Task dispatched to worker |
| `task.*completed` | Task finished successfully |
| `Circuit breaker.*CLOSED` | Docker operations resumed |

---

## Debug Commands

### Health Check

```bash
# Check manager health
docker exec hiclaw_hermes-manager curl -f http://localhost:8080/health/ready

# Check all services health
docker service ls --format "{{.Name}}: {{.Image}} - Replicas: {{.Replicas}}"
```

### Prometheus Metrics

```bash
# View all metrics
docker exec hiclaw_hermes-manager curl -s http://localhost:9090/metrics

# View specific metrics
docker exec hiclaw_hermes-manager curl -s http://localhost:9090/metrics | grep hiclaw_workers
docker exec hiclaw_hermes-manager curl -s http://localhost:9090/metrics | grep hiclaw_tasks
```

### Service Logs

```bash
# Manager logs (all)
docker service logs hiclaw_hermes-manager --tail 100

# Manager logs (follow)
docker service logs -f hiclaw_hermes-manager

# Worker logs
docker service logs hiclaw_hermes-worker-1 --tail 100

# Filter by pattern
docker service logs hiclaw_hermes-manager --grep "ERROR" --tail 50
```

### Database Inspection

```bash
# List databases
docker exec hiclaw_hermes-manager ls -la /root/.hermes/hiclaw/

# Query worker registry
docker exec hiclaw_hermes-manager sqlite3 /root/.hermes/hiclaw/workers-registry.db ".tables"
docker exec hiclaw_hermes-manager sqlite3 /root/.hermes/hiclaw/workers-registry.db "SELECT * FROM workers;"

# Query task queue
docker exec hiclaw_hermes-manager sqlite3 /root/.hermes/hiclaw/task-queue.db ".tables"
docker exec hiclaw_hermes-manager sqlite3 /root/.hermes/hiclaw/task-queue.db "SELECT * FROM tasks LIMIT 10;"
```

### Network Connectivity

```bash
# Test MinIO connectivity from manager
docker exec hiclaw_hermes-manager mc alias list
docker exec hiclaw_hermes-manager mc ls hiclaw/hiclaw-storage/

# Test Matrix connectivity
docker exec hiclaw_hermes-manager python3 -c "
from nio import AsyncClient
import os
client = AsyncClient(os.getenv('MATRIX_HOMESERVER'))
print('Matrix client created')
"

# Test Docker socket
docker exec hiclaw_hermes-manager docker ps
```

---

## Getting Help

If you continue to experience issues:

1. Collect diagnostic information:
   ```bash
   # Create diagnostics bundle
   docker service logs hiclaw_hermes-manager > manager.log
   docker service logs hiclaw_hermes-worker-1 > worker.log
   docker inspect hiclaw_hermes-manager > manager.json
   ```

2. Enable debug logging temporarily:
   ```bash
   docker service update --env HICLAW_LOG_LEVEL=DEBUG hiclaw_hermes-manager
   # Wait for issue to reproduce
   docker service logs --tail=500 hiclaw_hermes-manager > debug.log
   docker service update --env HICLAW_LOG_LEVEL=INFO hiclaw_hermes-manager
   ```

3. Check [GitHub Issues](https://github.com/NousResearch/hermes-agent/issues) for similar reports
