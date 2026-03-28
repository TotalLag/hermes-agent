# HiClaw Configuration Reference

Complete environment variable reference for HiClaw deployments.

---

## Core Configuration

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `HICLAW_LLM_API_KEY` | API key for LLM provider | - | Yes |
| `HICLAW_LLM_PROVIDER` | LLM provider name | `openai-compat` | No |
| `HICLAW_DEFAULT_MODEL` | Default model to use | `gpt-4o` | No |
| `HICLAW_WORKER_IMAGE` | Docker image for workers | `hermes-worker:latest` | No |
| `HICLAW_VERSION` | HiClaw version | `1.0.0` | No |

### LLM Provider Options

| Provider | `HICLAW_LLM_PROVIDER` | `HICLAW_OPENAI_BASE_URL` |
|----------|------------------------|--------------------------|
| OpenAI | `openai-compat` | `https://api.openai.com/v1` |
| OpenRouter | `openai-compat` | `https://openrouter.ai/v1` |
| Azure OpenAI | `azure` | Your Azure endpoint |
| Local/Ollama | `openai-compat` | `http://localhost:11434/v1` |

---

## Matrix Configuration

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `MATRIX_HOMESERVER` | Matrix server HTTP URL | `http://synapse:8008` | Yes |
| `HICLAW_MANAGER_MXID` | Manager's Matrix user ID | - | Yes |
| `HICLAW_MANAGER_PASSWORD` | Manager's Matrix password | - | Yes |
| `HICLAW_MANAGER_ACCESS_TOKEN` | Manager's Matrix access token | - | Yes |
| `HICLAW_REGISTRATION_TOKEN` | Token for worker registration | - | Yes |
| `HICLAW_ADMIN_USER` | Admin Matrix user ID | - | No |
| `HICLAW_ADMIN_PASSWORD` | Admin Matrix password | - | No |

### Matrix Reconnection

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `MATRIX_RECONNECT_ENABLED` | Enable automatic Matrix reconnection | `true` | No |
| `MATRIX_RECONNECT_MAX_DELAY` | Maximum delay between retry attempts (seconds) | `60` | No |

The Matrix reconnection feature uses exponential backoff with jitter:
- Initial delay: 1.0 second
- Multiplier: 2.0
- Jitter: 10%
- Maximum delay: `MATRIX_RECONNECT_MAX_DELAY`

Example: With default settings, retry attempts occur at ~1s, ~2s, ~4s, ~8s, ~16s, ~32s, then capped at 60s.

---

## Worker Management

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `HICLAW_WORKER_COUNT` | Number of workers to spawn | `1` | No |
| `HICLAW_WORKER_NAME` | Worker name suffix | - | Yes (worker) |
| `HICLAW_WORKER_VERSION` | Worker version | `1.0.0` | No |
| `HICLAW_WORKER_HEARTBEAT_INTERVAL` | Heartbeat interval (seconds) | `120` | No |
| `HICLAW_MANAGER_CHECK_INTERVAL` | Manager poll interval (seconds) | `300` | No |

### Stale Worker Cleanup

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `HICLAW_STALE_CLEANUP_ENABLED` | Enable automatic stale cleanup | `true` | No |
| `HICLAW_STALE_CLEANUP_INTERVAL` | Cleanup run interval (seconds) | `300` | No |
| `HICLAW_STALE_THRESHOLD_SECONDS` | Seconds without heartbeat to mark stale | `300` | No |

The stale cleanup system:
1. Runs as a cron job every `HICLAW_STALE_CLEANUP_INTERVAL` seconds
2. Marks workers offline if no heartbeat for `HICLAW_STALE_THRESHOLD_SECONDS`
3. Fails orphaned tasks that have been running too long

Recommended: Set `HICLAW_STALE_THRESHOLD_SECONDS` to at least 2-3x the `HICLAW_WORKER_HEARTBEAT_INTERVAL`.

---

## Storage Configuration

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `HICLAW_MINIO_ENDPOINT` | MinIO server endpoint | `minio:9000` | Yes |
| `HICLAW_MINIO_ACCESS_KEY` | MinIO access key | `hiclawadmin` | No |
| `HICLAW_MINIO_SECRET_KEY` | MinIO secret key | `hiclawsecretpass` | No |
| `HICLAW_MINIO_BUCKET` | MinIO bucket name | `hiclaw` | No |
| `HICLAW_MINIO_SECURE` | Use TLS for MinIO | `false` | No |
| `HICLAW_STORAGE_BUCKET` | Storage bucket name | Same as `HICLAW_MINIO_BUCKET` | No |
| `HICLAW_STORAGE_PREFIX` | Path prefix in bucket | `agents` | No |
| `HICLAW_TASK_SPECS_PREFIX` | Task specs path prefix | `task-specs/` | No |
| `HICLAW_TASK_RESULTS_PREFIX` | Task results path prefix | `task-results/` | No |

---

## Monitoring Configuration

### Logging

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `HICLAW_LOG_LEVEL` | Log verbosity | `INFO` | No |
| `HICLAW_LOG_JSON` | Use JSON log format | `true` | No |
| `HICLAW_LOG_FILE` | Optional log file path | - | No |

Valid `HICLAW_LOG_LEVEL` values (case-insensitive):
- `DEBUG` - Detailed information for debugging
- `INFO` - General operational information
- `WARNING` - Warning conditions
- `ERROR` - Error conditions

When `HICLAW_LOG_JSON=true`, logs are output in structured JSON format:
```json
{
  "timestamp": "2024-01-01T12:00:00.000Z",
  "level": "INFO",
  "logger": "gateway.hiclaw.manager_handler",
  "message": "Worker registered",
  "extra_fields": {...}
}
```

### Prometheus Metrics

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `HICLAW_METRICS_ENABLED` | Enable Prometheus metrics | `true` | No |
| `HICLAW_METRICS_PORT` | Metrics HTTP server port | `9090` | No |
| `HICLAW_HEALTH_PORT` | Health check port | `8080` | No |

Metrics are exposed at:
- `http://localhost:{HICLAW_METRICS_PORT}/metrics` - Prometheus scrape endpoint
- `http://localhost:{HICLAW_HEALTH_PORT}/health/ready` - Kubernetes readiness probe
- `http://localhost:{HICLAW_HEALTH_PORT}/health/live` - Kubernetes liveness probe

### Available Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `hiclaw_workers_total` | Gauge | Total registered workers |
| `hiclaw_workers_ready` | Gauge | Workers in ready state |
| `hiclaw_tasks_pending` | Gauge | Tasks awaiting assignment |
| `hiclaw_tasks_completed_total` | Counter | Completed tasks |
| `hiclaw_tasks_failed_total` | Counter | Failed tasks |
| `hiclaw_heartbeat_latency_seconds` | Histogram | Heartbeat round-trip time |

---

## Docker Configuration

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `DOCKER_HOST` | Docker socket path | `unix:///var/run/docker.sock` | No |
| `HICLAW_WORKER_IMAGE` | Worker container image | `hermes-worker:latest` | No |

### Docker Circuit Breaker

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `HICLAW_DOCKER_CIRCUIT_BREAKER_THRESHOLD` | Failures before opening circuit | `3` | No |
| `HICLAW_DOCKER_CIRCUIT_BREAKER_TIMEOUT` | Recovery timeout (seconds) | `300` | No |

The circuit breaker prevents cascading failures when Docker operations fail repeatedly:
- Opens after `HICLAW_DOCKER_CIRCUIT_BREAKER_THRESHOLD` failures in a 5-minute window
- Stays open for `HICLAW_DOCKER_CIRCUIT_BREAKER_TIMEOUT` seconds
- Transitions to HALF_OPEN to test recovery
- Closes on successful operation

---

## Security Configuration

### Secrets Management

For production deployments, store secrets using Docker secrets:

```bash
# Create secrets
echo -n "your-api-key" | docker secret create hiclaw-llm-api-key -
echo -n "your-token" | docker secret create hiclaw-manager-token -

# Reference in docker-compose.yml
 secrets:
   - hiclaw-llm-api-key
   - hiclaw-manager-token
```

### Network Security

| Variable | Description | Default |
|----------|-------------|---------|
| `HICLAW_DOCKER_PROXY` | Enable Docker proxy API | `1` |

The Docker proxy allows controlled container spawning from within the manager.

---

## Environment Variable Files

### hiclaw.env (Non-Secret Configuration)

```bash
# Infrastructure
SYNAPSE_SERVER_NAME=hermes.local
HICLAW_MINIO_ACCESS_KEY=hiclawadmin
HICLAW_MINIO_SECRET_KEY=hiclawsecretpass
HICLAW_MINIO_BUCKET=hiclaw

# LLM Provider
HICLAW_LLM_PROVIDER=openai-compat
HICLAW_DEFAULT_MODEL=gpt-4o

# Worker Management
HICLAW_REGISTRATION_TOKEN=change_me
HICLAW_WORKER_HEARTBEAT_INTERVAL=120
HICLAW_MANAGER_CHECK_INTERVAL=300
HICLAW_STALE_THRESHOLD_SECONDS=300
```

### hiclaw-secrets.env (Secret Configuration)

```bash
# LLM API Key
HICLAW_LLM_API_KEY=sk-your-key-here

# Matrix Access Tokens
HICLAW_MANAGER_ACCESS_TOKEN=your-manager-token
HICLAW_WORKER1_ACCESS_TOKEN=your-worker1-token
```

---

## Full Variable List

| Variable | Default | Category |
|----------|---------|----------|
| `DOCKER_HOST` | `unix:///var/run/docker.sock` | Docker |
| `HICLAW_DEFAULT_MODEL` | `gpt-4o` | Core |
| `HICLAW_DOCKER_CIRCUIT_BREAKER_THRESHOLD` | `3` | Docker |
| `HICLAW_DOCKER_CIRCUIT_BREAKER_TIMEOUT` | `300` | Docker |
| `HICLAW_DOCKER_PROXY` | `1` | Docker |
| `HICLAW_HEALTH_PORT` | `8080` | Monitoring |
| `HICLAW_LLM_API_KEY` | - | Core |
| `HICLAW_LLM_PROVIDER` | `openai-compat` | Core |
| `HICLAW_LOG_FILE` | - | Monitoring |
| `HICLAW_LOG_JSON` | `true` | Monitoring |
| `HICLAW_LOG_LEVEL` | `INFO` | Monitoring |
| `HICLAW_MANAGER_ACCESS_TOKEN` | - | Matrix |
| `HICLAW_MANAGER_CHECK_INTERVAL` | `300` | Workers |
| `HICLAW_MANAGER_MXID` | - | Matrix |
| `HICLAW_MANAGER_PASSWORD` | - | Matrix |
| `HICLAW_METRICS_ENABLED` | `true` | Monitoring |
| `HICLAW_METRICS_PORT` | `9090` | Monitoring |
| `HICLAW_MINIO_ACCESS_KEY` | `hiclawadmin` | Storage |
| `HICLAW_MINIO_BUCKET` | `hiclaw` | Storage |
| `HICLAW_MINIO_ENDPOINT` | `minio:9000` | Storage |
| `HICLAW_MINIO_SECURE` | `false` | Storage |
| `HICLAW_MINIO_SECRET_KEY` | `hiclawsecretpass` | Storage |
| `HICLAW_OPENAI_BASE_URL` | `https://api.openai.com/v1` | Core |
| `HICLAW_REGISTRATION_TOKEN` | - | Matrix |
| `HICLAW_STALE_CLEANUP_ENABLED` | `true` | Workers |
| `HICLAW_STALE_CLEANUP_INTERVAL` | `300` | Workers |
| `HICLAW_STALE_THRESHOLD_SECONDS` | `300` | Workers |
| `HICLAW_STORAGE_BUCKET` | Same as `HICLAW_MINIO_BUCKET` | Storage |
| `HICLAW_STORAGE_PREFIX` | `agents` | Storage |
| `HICLAW_TASK_RESULTS_PREFIX` | `task-results/` | Storage |
| `HICLAW_TASK_SPECS_PREFIX` | `task-specs/` | Storage |
| `HICLAW_VERSION` | `1.0.0` | Core |
| `HICLAW_WORKER_COUNT` | `1` | Workers |
| `HICLAW_WORKER_HEARTBEAT_INTERVAL` | `120` | Workers |
| `HICLAW_WORKER_IMAGE` | `hermes-worker:latest` | Core |
| `HICLAW_WORKER_NAME` | - | Workers |
| `HICLAW_WORKER_VERSION` | `1.0.0` | Workers |
| `MATRIX_HOMESERVER` | `http://synapse:8008` | Matrix |
| `MATRIX_RECONNECT_ENABLED` | `true` | Matrix |
| `MATRIX_RECONNECT_MAX_DELAY` | `60` | Matrix |
| `SYNAPSE_SERVER_NAME` | `hermes.local` | Matrix |
| `SYNAPSE_REGISTRATION_SHARED_SECRET` | - | Matrix |
