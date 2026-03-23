# Hermes Agent — Hiclaw Worker Integration

This directory contains Hermes Agent worker integration for the Hiclaw distributed task execution platform. Hermes runs as a worker node that can register with a Hiclaw Manager and receive tasks for execution.

## Overview

Hiclaw is a distributed agent orchestration platform where workers register with a Manager to receive and execute tasks. The Hermes Agent acts as a worker in this architecture, providing full tool-calling capabilities, memory, skills, and all other Hermes features to the Hiclaw ecosystem.

## Architecture

```
┌─────────────────┐
│  Hiclaw Manager │
│  (Task Broker)  │
└────────┬────────┘
         │
         │ Tasks distributed
         ↓
┌─────────────────┐
│  Hermes Worker  │
│  (This image)   │
└─────────────────┘
    │
    ├─> Terminal tool execution
    ├─> Web search and browser automation
    ├─> File operations
    ├─> Memory and skills system
    └─> All Hermes capabilities
```

### Components

1. **Hiclaw Manager**: Central task broker that manages worker registration, task distribution, and result collection
2. **Hermes Worker**: Docker container running Hermes Agent that registers with Manager and executes tasks
3. **Task Queue**: Manager maintains a queue of tasks and distributes them to available workers
4. **Registration Protocol**: Workers announce their capabilities and availability to Manager via HTTP API

## Prerequisites

### Software Requirements

- **Docker**: Version 24.0 or later
  ```bash
  docker --version  # Must be >= 24.0
  ```

- **Docker Compose**: Version 2.20 or later (optional, for multi-container deployments)
  ```bash
  docker compose version
  ```

- **Git**: For cloning the repository
  ```bash
  git --version  # Any recent version
  ```

### Infrastructure Requirements

- **Hiclaw Manager**: A running Hiclaw Manager instance (deployed separately via TotalLag/hiclaw)
- **Network Access**: Worker must be able to reach Hiclaw Manager via HTTP/HTTPS
- **Container Registry**: Access to push/pull Docker images (for deployment)

### Hardware Requirements

Minimum:
- **CPU**: 1 core
- **RAM**: 512 MB
- **Disk**: 5 GB

Recommended:
- **CPU**: 2+ cores
- **RAM**: 2 GB+
- **Disk**: 20 GB+ (for skills, logs, and caching)

## Installation

### 1. Clone Repository

```bash
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
```

### 2. Build Docker Image

```bash
cd hiclaw
make build
```

This builds a Docker image tagged as `hermes-worker:latest`.

Alternatively, build directly with Docker:

```bash
docker build \
  -f hiclaw/Dockerfile.worker \
  -t hermes-worker:latest \
  --label "maintainer=Hiclaw Team" \
  --label "description=Hermes Hiclaw Worker" \
  .
```

### 3. Configure Environment Variables

Create an `.env` file in the hiclaw directory or set environment variables at runtime:

```bash
# Required for registration with Hiclaw Manager
export HICLAW_MANAGER_URL="https://manager.your-domain.com"
export HICLAW_WORKER_ID="hermes-worker-1"
export HICLAW_WORKER_SECRET="your-secure-secret-here"

# Optional: Worker capabilities and metadata
export HICLAW_CAPABILITIES="terminal,web,file,browser,vision"
export HICLAW_MAX_CONCURRENT_TASKS="3"

# Hermes Agent configuration
export HERMES_ENDPOINT="http://localhost:8080"
export LOG_LEVEL="info"
```

### 4. Run Worker

#### Option A: Direct Docker Run

```bash
docker run --rm \
  --name hermes-worker \
  -e "HICLAW_MANAGER_URL=https://manager.your-domain.com" \
  -e "HICLAW_WORKER_ID=hermes-worker-1" \
  -e "HICLAW_WORKER_SECRET=your-secret" \
  -e "HICLAW_ENV=production" \
  -e "LOG_LEVEL=info" \
  -p "8081:8080" \
  hermes-worker:latest
```

#### Option B: Using Make

```bash
make run
```

This uses the default environment variables from the Makefile. Override by setting them before running:

```bash
HICLAW_MANAGER_URL=https://manager.your-domain.com \
make run
```

#### Option C: Docker Compose (Recommended for Production)

Create a `docker-compose.yml` in the hiclaw directory:

```yaml
version: "3.8"

services:
  hermes-worker:
    image: hermes-worker:latest
    container_name: hermes-worker-1
    restart: unless-stopped
    ports:
      - "8081:8080"
    environment:
      - HICLAW_MANAGER_URL=https://manager.your-domain.com
      - HICLAW_WORKER_ID=hermes-worker-1
      - HICLAW_WORKER_SECRET=${HICLAW_WORKER_SECRET}
      - HICLAW_ENV=production
      - LOG_LEVEL=info
    volumes:
      - hermes-data:/workspace
      - ./skills:/workspace/skills:ro
      - ./logs:/workspace/logs

volumes:
  hermes-data:
```

Run with:

```bash
docker compose up -d
```

## Configuration

### Environment Variables

| Variable | Required | Description | Default |
|-----------|-----------|-------------|----------|
| `HICLAW_MANAGER_URL` | Yes | URL of Hiclaw Manager API | - |
| `HICLAW_WORKER_ID` | Yes | Unique identifier for this worker | - |
| `HICLAW_WORKER_SECRET` | Yes | Secret for authentication with Manager | - |
| `HICLAW_ENV` | No | Environment: development, staging, production | development |
| `HERMES_ENDPOINT` | No | Hermes Agent HTTP endpoint | http://localhost:8080 |
| `LOG_LEVEL` | No | Logging verbosity: debug, info, warn, error | info |
| `HICLAW_CAPABILITIES` | No | Comma-separated list of worker capabilities | All Hermes tools |
| `HICLAW_MAX_CONCURRENT_TASKS` | No | Maximum concurrent tasks | 3 |
| `HICLAW_REGISTRY` | No | Docker registry URL for `make deploy` | - |

### Config Transformation: openclaw.json → config.yaml

Hermes Agent historically used `openclaw.json` for configuration. Hiclaw integration uses the modern `config.yaml` format. If migrating from an existing OpenClaw setup, transform your configuration:

**openclaw.json (legacy):**

```json
{
  "model": "anthropic/claude-opus-4.6",
  "provider": "openrouter",
  "api_key": "sk-...",
  "tools": ["terminal", "web", "file"]
}
```

**config.yaml (modern):**

```yaml
model:
  default: "anthropic/claude-opus-4.6"
  provider: "openrouter"
  base_url: "https://openrouter.ai/api/v1"

terminal:
  backend: "docker"
  cwd: "/workspace"
  timeout: 180

toolsets:
  - hermes-cli
```

The transformation preserves all settings while moving to a structured YAML format. Use `hermes claw migrate` for automatic migration if you have an existing OpenClaw installation.

## Deployment

### Local Deployment

For development or testing on a single machine:

```bash
cd hiclaw
make build
make run
```

The worker will start and attempt to register with the Hiclaw Manager specified in environment variables.

### Container Registry Deployment

To deploy to a Docker registry (Docker Hub, ECR, GCR, etc.):

```bash
export HICLAW_REGISTRY=registry.example.com:5000
make deploy
```

This builds the image, tags it for the registry, and pushes it:

1. Tests the worker image (`make test`)
2. Tags image as `${REGISTRY}/hermes-worker:latest`
3. Pushes to registry

### Kubernetes Deployment

Create a `deployment.yaml` for Kubernetes:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hermes-worker
spec:
  replicas: 3
  selector:
    matchLabels:
      app: hermes-worker
  template:
    metadata:
      labels:
        app: hermes-worker
    spec:
      containers:
      - name: hermes-worker
        image: registry.example.com/hermes-worker:latest
        ports:
          - containerPort: 8080
        env:
        - name: HICLAW_MANAGER_URL
          value: "https://manager.your-domain.com"
        - name: HICLAW_WORKER_ID
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
        - name: HICLAW_WORKER_SECRET
          valueFrom:
            secretKeyRef:
              name: hiclaw-secrets
              key: worker-secret
        - name: LOG_LEVEL
          value: "info"
        resources:
          requests:
            cpu: "500m"
            memory: "512Mi"
          limits:
            cpu: "2000m"
            memory: "2Gi"
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
```

Apply with:

```bash
kubectl apply -f deployment.yaml
```

## Worker Registration

When the Hermes Worker container starts, it automatically registers with the Hiclaw Manager:

### Registration Process

1. **Worker Startup**: Container starts, loads configuration from environment variables
2. **Announce**: Worker sends POST request to `HICLAW_MANAGER_URL/api/workers/register`
3. **Authentication**: Worker authenticates using `HICLAW_WORKER_ID` and `HICLAW_WORKER_SECRET`
4. **Capabilities**: Worker reports available tools, terminal backends, and constraints
5. **Heartbeat**: Worker sends periodic heartbeat to Manager to signal availability
6. **Task Reception**: Manager assigns tasks via POST to worker's `/tasks` endpoint

### Registration Payload

```json
{
  "worker_id": "hermes-worker-1",
  "capabilities": [
    "terminal",
    "web_search",
    "file_read",
    "file_write",
    "browser_automation",
    "vision_analyze",
    "skills_system"
  ],
  "constraints": {
    "max_concurrent_tasks": 3,
    "terminal_backend": "docker",
    "max_session_time": 3600,
    "supported_languages": ["python", "bash", "javascript"]
  },
  "endpoints": {
    "task_submission": "http://worker-ip:8080/tasks",
    "status": "http://worker-ip:8080/status",
    "health": "http://worker-ip:8080/health"
  }
}
```

### Managing Workers

List registered workers:

```bash
curl https://manager.your-domain.com/api/workers
```

Worker status:

```bash
curl https://manager.your-domain.com/api/workers/hermes-worker-1/status
```

Deregister a worker:

```bash
curl -X DELETE \
  -H "Authorization: Bearer YOUR-ADMIN-TOKEN" \
  https://manager.your-domain.com/api/workers/hermes-worker-1
```

## Troubleshooting

### Worker Fails to Start

**Symptoms**: Container exits immediately or won't start.

**Possible Causes**:
1. Missing environment variables
2. Invalid Dockerfile syntax
3. Port conflicts (8081 already in use)

**Solutions**:

```bash
# Check container logs
docker logs hermes-worker

# Check port availability
netstat -tuln | grep 8081

# Verify environment variables
docker run --rm \
  -e "HICLAW_MANAGER_URL=$HICLAW_MANAGER_URL" \
  -e "HICLAW_WORKER_ID=$HICLAW_WORKER_ID" \
  -e "HICLAW_WORKER_SECRET=$HICLAW_WORKER_SECRET" \
  hermes-worker:latest \
  env | grep HICLAW
```

### Registration Fails

**Symptoms**: Worker starts but Manager doesn't show it as registered.

**Possible Causes**:
1. Manager URL is incorrect or unreachable
2. Worker secret doesn't match Manager's record
3. Network firewall blocking connection
4. Worker ID already registered with different secret

**Solutions**:

```bash
# Test Manager connectivity
curl -v https://manager.your-domain.com/api/health

# Check worker logs for registration errors
docker logs hermes-worker | grep -i "registration\|error"

# Verify credentials match exactly
echo "Worker ID: $HICLAW_WORKER_ID"
echo "Secret: $HICLAW_WORKER_SECRET"

# Check firewall rules
sudo ufw status
sudo iptables -L -n
```

### Tasks Timeout or Fail

**Symptoms**: Tasks assigned but never complete, or fail with timeout errors.

**Possible Causes**:
1. Worker is overloaded (too many concurrent tasks)
2. LLM API rate limits
3. Insufficient resources (CPU/RAM)
4. Task timeout too short for complex work

**Solutions**:

```bash
# Reduce concurrent tasks
export HICLAW_MAX_CONCURRENT_TASKS=1
docker restart hermes-worker

# Check resource usage
docker stats hermes-worker

# Increase timeout in task submission or worker config
export HICLAW_DEFAULT_TIMEOUT=300  # 5 minutes

# Verify LLM API quota and rate limits
# Check provider dashboard (OpenRouter, Nous Portal, etc.)
```

### Health Check Failures

**Symptoms**: Manager reports worker as unhealthy, worker gets deregistered.

**Possible Causes**:
1. Worker process crashed
2. Health check endpoint changed
3. Network partition (worker can't reach Manager)

**Solutions**:

```bash
# Check if container is running
docker ps | grep hermes-worker

# Check worker process
docker exec hermes-worker ps aux | grep hermes

# Manually test health endpoint
curl http://localhost:8081/health

# Restart worker
docker restart hermes-worker
```

### Docker Build Errors

**Symptoms**: `docker build` fails with syntax or dependency errors.

**Common Issues**:

1. **Base image pull failure**:
   ```bash
   # Error: failed to pull image
   # Solution: Check internet connection and image name
   docker pull nikolaik/python-nodejs:python3.11-nodejs20
   ```

2. **COPY file not found**:
   ```bash
   # Error: COPY failed: file not found
   # Solution: Ensure you're building from project root
   cd hermes-agent
   docker build -f hiclaw/Dockerfile.worker .
   ```

3. **Python dependency conflicts**:
   ```bash
   # Error: pip install failed
   # Solution: Check requirements.txt versions match Python 3.11
   python --version  # Must be 3.11
   ```

### Performance Issues

**Symptoms**: Worker responds slowly, high latency on tasks.

**Optimization Strategies**:

1. **Increase resources**:
   ```yaml
   # docker-compose.yml
   services:
     hermes-worker:
         deploy:
           resources:
             limits:
                 cpus: '4.0'
                 memory: 4G
   ```

2. **Enable context compression** (already enabled by default):
   ```yaml
   # config.yaml
   compression:
     enabled: true
     threshold: 0.85  # Compress at 85% of context
   ```

3. **Use faster auxiliary models**:
   ```yaml
   # config.yaml
   auxiliary:
     vision:
       provider: "openrouter"
       model: "google/gemini-2.5-flash"
   ```

4. **Reduce logging overhead**:
   ```bash
   export LOG_LEVEL=warn  # Only log warnings and errors
   docker restart hermes-worker
   ```

## Additional Resources

- [Hermes Agent Documentation](https://hermes-agent.nousresearch.com/docs/)
- [Hiclaw Architecture](https://github.com/TotalLag/hiclaw)
