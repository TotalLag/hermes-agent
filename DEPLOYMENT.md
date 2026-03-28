# HiClaw Production Deployment Guide

This guide covers production deployment of HiClaw using Docker Swarm with secrets management, health checks, and rolling updates.

---

## Prerequisites

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| Docker Version | 24.0+ | 25.0+ |
| RAM | 4 GB | 8 GB |
| CPU | 2 cores | 4 cores |
| Docker Swarm | Enabled | Active manager node |

### Docker Swarm Setup

Docker Swarm is required for:
- Docker secrets (secure credential storage)
- Rolling updates with health checks
- Service logging with JSON driver
- Network overlay for service discovery

```bash
# Initialize Docker Swarm (if not already done)
docker swarm init

# Create overlay network for HiClaw services
docker network create --driver overlay hiclaw-prod-net
```

---

## Step-by-Step Deployment

### 1. Clone Repository

```bash
git clone https://github.com/TotalLag/hermes-agent.git
cd hermes-agent
```

### 2. Create Secrets

Create a Docker secrets file for production credentials:

```bash
# Copy the example secrets file
cp docker/hiclaw-secrets.env.example docker/hiclaw-secrets.env

# Edit with actual values
vim docker/hiclaw-secrets.env
```

Required secrets:
- `HICLAW_LLM_API_KEY` - Your LLM provider API key
- `HICLAW_MANAGER_ACCESS_TOKEN` - Matrix access token for manager
- `HICLAW_WORKER1_ACCESS_TOKEN` - Matrix access token for worker

```bash
# Validate secrets file syntax
source docker/hiclaw-secrets.env && echo "Secrets loaded OK"
```

### 3. Configure hiclaw.env

Copy and configure the environment file:

```bash
cp docker/hiclaw.env.example docker/hiclaw.env
vim docker/hiclaw.env
```

Key settings to verify:
- `SYNAPSE_SERVER_NAME` - Your Matrix server name
- `HICLAW_MINIO_ACCESS_KEY` / `HICLAW_MINIO_SECRET_KEY` - MinIO credentials
- `HICLAW_REGISTRATION_TOKEN` - Token workers use to register

### 4. Build Images

```bash
# Build manager image
docker build -f docker/Dockerfile.manager -t hermes-manager:latest .

# Build worker image
docker build -f docker/Dockerfile.worker -t hermes-worker:latest .

# Tag for local registry (required for Docker proxy allowlist)
docker tag hermes-manager:latest localhost/hermes-manager:latest
docker tag hermes-worker:latest localhost/hermes-worker:latest
```

### 5. Deploy Stack

```bash
# Create secret files from env (Docker Swarm reads from files)
# Note: Docker secrets require files, not env vars directly

# Create individual secrets
echo -n "your-llm-api-key" | docker secret create hiclaw-llm-api-key -
echo -n "your-manager-token" | docker secret create hiclaw-manager-token -
echo -n "your-worker-token" | docker secret create hiclaw-worker-token -

# Deploy using production compose
docker stack deploy -c docker-compose.yml -c docker-compose.prod.yml hiclaw
```

### 6. Verify Deployment

```bash
# Check stack services
docker stack services hiclaw

# Check service logs
docker service logs hiclaw_hermes-manager --tail 50
docker service logs hiclaw_hermes-worker-1 --tail 50

# Check service health
docker service ls
```

---

## Docker Swarm Stack File

The production stack (`docker-compose.prod.yml`) extends the base configuration with:

```yaml
services:
  hermes-manager:
    deploy:
      replicas: 1
      resources:
        limits:
          cpus: "1.0"
          memory: 1G
        reservations:
          cpus: "0.5"
          memory: 512M
    restart: unless-stopped
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    env_file:
      - hiclaw-secrets.env
```

---

## Rolling Updates

### Update Manager Image

```bash
# Build new image
docker build -f docker/Dockerfile.manager -t hermes-manager:latest .

# Update service with rolling health check
docker service update \
  --image hermes-manager:latest \
  --update-delay 30s \
  --update-parallelism 1 \
  --health-cmd "curl -f http://localhost:8080/health/ready || exit 1" \
  --health-interval 10s \
  --health-timeout 5s \
  --health-retries 3 \
  hiclaw_hermes-manager
```

### Update Worker Image

```bash
# Build new image
docker build -f docker/Dockerfile.worker -t hermes-worker:latest .

# Update workers (can do in parallel since workers are stateless)
docker service update \
  --image hermes-worker:latest \
  --update-delay 10s \
  --update-parallelism 2 \
  hiclaw_hermes-worker-1
```

### Monitor Update Progress

```bash
# Watch update status
docker service inspect hiclaw_hermes-manager --pretty | grep -A5 Update

# Check for any failed tasks
docker service ps hiclaw_hermes-manager
```

---

## Rollback Procedure

### Rollback to Previous Version

```bash
# Rollback manager to previous version
docker service rollback hiclaw_hermes-manager

# Rollback worker to previous version
docker service rollback hiclaw_hermes-worker-1

# Verify rollback
docker service inspect hiclaw_hermes-manager --pretty | grep Image
```

### Manual Rollback (Image Reference)

```bash
# Force update to specific image digest
docker service update \
  --image hermes-manager:previous@sha256:abc123... \
  hiclaw_hermes-manager
```

---

## Backup Strategy

### SQLite Database Locations

HiClaw uses SQLite databases for state:
- `~/.hermes/hiclaw/workers-registry.db` - Worker registry
- `~/.hermes/hiclaw/task-queue.db` - Task queue

### Automated Backup Script

Create `/opt/hiclaw/backup.sh`:

```bash
#!/bin/bash
set -euo pipefail

BACKUP_DIR="/var/backups/hiclaw"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
CONTAINER_NAME="hiclaw_hermes-manager"

# Create backup directory
mkdir -p "${BACKUP_DIR}"

# Get manager container ID
CONTAINER_ID=$(docker ps -q --filter "name=${CONTAINER_NAME}" | head -1)

if [ -z "${CONTAINER_ID}" ]; then
    echo "ERROR: Manager container not running"
    exit 1
fi

# Backup databases
docker cp "${CONTAINER_ID}:/root/.hermes/hiclaw/workers-registry.db" \
    "${BACKUP_DIR}/workers-registry-${TIMESTAMP}.db"

docker cp "${CONTAINER_ID}:/root/.hermes/hiclaw/task-queue.db" \
    "${BACKUP_DIR}/task-queue-${TIMESTAMP}.db"

# Compress backups
gzip "${BACKUP_DIR}/workers-registry-${TIMESTAMP}.db"
gzip "${BACKUP_DIR}/task-queue-${TIMESTAMP}.db"

# Remove backups older than 7 days
find "${BACKUP_DIR}" -name "*.db.gz" -mtime +7 -delete

echo "Backup completed: ${TIMESTAMP}"
```

### Cron Setup

```bash
# Add to crontab
echo "0 * * * * /opt/hiclaw/backup.sh" | crontab -
```

This runs backups every hour.

---

## Health Checks

### Manager Health Endpoint

```bash
# Inside container
curl http://localhost:8080/health/ready

# From host
docker exec hiclaw_hermes-manager curl -f http://localhost:8080/health/ready
```

### Prometheus Metrics

```bash
# Inside container
curl http://localhost:9090/metrics

# From host
docker exec hiclaw_hermes-manager curl -f http://localhost:9090/metrics
```

### Docker Health Check

```bash
# Check container health status
docker inspect --format='{{.State.Health.Status}}' hiclaw_hermes-manager
```

---

## Network Configuration

### Required Ports

| Service | Port | Purpose |
|---------|------|---------|
| Manager | 8080 | Health/metrics endpoints |
| Metrics | 9090 | Prometheus scrape endpoint |
| Synapse | 8008 | Matrix client API |
| MinIO | 9000 | Object storage API |

### Network Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    hiclaw-prod-net (overlay)             │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │   synapse    │  │    minio     │  │   manager    │ │
│  │   :8008      │  │    :9000     │  │   :8080      │ │
│  └──────────────┘  └──────────────┘  │   :9090      │ │
│                                       └──────────────┘ │
│                                                │        │
│                                       ┌──────────────┐ │
│                                       │   worker-1   │ │
│                                       └──────────────┘ │
└─────────────────────────────────────────────────────────┘
```

---

## Environment Variables Reference

For complete environment variable documentation, see [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md).

Key production variables:

| Variable | Description | Example |
|----------|-------------|---------|
| `HICLAW_LOG_LEVEL` | Log verbosity | `INFO` |
| `HICLAW_METRICS_ENABLED` | Enable Prometheus metrics | `true` |
| `HICLAW_STALE_CLEANUP_ENABLED` | Enable stale worker cleanup | `true` |
| `MATRIX_RECONNECT_ENABLED` | Enable Matrix auto-reconnect | `true` |
