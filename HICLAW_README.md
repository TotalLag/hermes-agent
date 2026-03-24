# Hermes Agent as hiclaw Manager — Integration Guide

This document describes how to use **hermes-agent** as the Manager replacement in a hiclaw deployment, replacing OpenClaw. Workers communicate with the Manager via Matrix rooms and store/retrieve task specs via MinIO.

---

## What Was Done

The `TotalLag/hermes-agent` fork (`main` branch, commit `9789fda`) adds a full hiclaw worker integration layer under `hermes-agent/hiclaw/`:

### Files Added/Modified

| File | Purpose |
|------|---------|
| `hiclaw/Dockerfile.worker` | Multi-stage build producing a minimal worker container |
| `hiclaw/scripts/hermes-entrypoint.sh` | Container entrypoint: pulls config from MinIO, transforms it, registers worker, launches gateway |
| `hiclaw/scripts/create-hermes-worker.sh` | Worker registration script: sends Matrix messages to the Manager room |
| `hiclaw/scripts/hiclaw_config_transform.py` | Transforms `openclaw.json` → `config.yaml` at startup |
| `tools/hiclaw_manager_tool.py` | `hiclaw_create_worker` tool: spawns hermes-worker containers via Docker proxy API |
| `manager/hermes-entrypoint.sh` | Manager container entrypoint (Manager mode) |
| `manager/config.env` | Manager configuration defaults |

### Key Implementation Decisions

- **`hermes gateway run`** (not `hermes gateway start`): `start` defaults to systemd which doesn't exist in containers. `run` runs the gateway process directly.
- **`HICLAW_MATRIX_INTERNAL_URL`** for HTTP API calls: The Matrix registration/send API uses HTTP (not HTTPS) to avoid SSL cert issues on the internal Docker network. The nio Matrix sync client uses `HICLAW_MATRIX_HOMESERVER` (external HTTPS).
- **`HICLAW_MINIO_HOST`** defaulting to `http://hiclaw-manager:9000`: Inside worker containers on `hiclaw-net`, `hiclaw-manager` resolves to the same IP as MinIO (`172.21.0.3`). The hostname `minio` does NOT resolve inside containers.
- **`HICLAW_MATRIX_INTERNAL_URL`** defaulting to `http://hiclaw-manager:6167`: The Matrix/Tuwunel HTTP API is on port 6167. Using `127.0.0.1` fails because it is loopback inside the container.
- **Non-editable pip install**: The `uv pip install -e ".[all]"` form is not used in the Dockerfile — editable installs embed absolute build-stage paths that don't exist at runtime.
- **Worker config path**: Config is stored at `agents/${HICLAW_WORKER_NAME}/openclaw.json` in MinIO, not at the bucket root.
- **`HICLAW_HERMES_MODE=gateway`**: Workers listen for Matrix messages via the gateway's MatrixAdapter, not interactive CLI mode.
- **Container image tag `localhost/hermes-worker:latest`**: The Docker proxy on hiclaw only allows `localhost`, `local`, Higress registry, or configured registries. `ghcr.io/totallag/hermes-worker:latest` is blocked. Build locally and tag as `localhost/hermes-worker:latest`, or push to a configured registry.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Hermes Worker Container                         │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ hermes-entrypoint.sh                                             │ │
│  │   1. pull_config()      → MinIO: agents/${name}/openclaw.json   │ │
│  │   2. transform_config() → openclaw.json → config.yaml           │ │
│  │   3. register_worker()  → Matrix room: send registration msg    │ │
│  │   4. launch_gateway()   → hermes gateway run                    │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  Scripts:               Config:                Tools:                     │
│  - hermes-entrypoint.sh   openclaw.json        mc (MinIO Client)        │
│  - create-hermes-worker  config.yaml           hermes (AIAgent)          │
│  - hiclaw_config_transform                                           │
└──────────────────────────────────────────────────────────────────────┘
                                   │
              ┌────────────────────┴────────────────────┐
              │         hiclaw-net (Docker)               │
              ▼                                          ▼
┌──────────────────────────────┐      ┌───────────────────────────────────┐
│        MinIO               │      │         Matrix (Tuwunel)          │
│  Config + Task Specs       │      │  Worker ↔ Manager room comms      │
│  Port 9000                 │      │  Port 6167                       │
└──────────────────────────────┘      └───────────────────────────────────┘
              ▲                                          ▲
              │         ┌────────────────────────────────┘
              │         │
┌──────────────────────────────────────────────────────────────────────┐
│                      hiclaw Manager (hermes-agent)                   │
│  - Task dispatch via Matrix room                                     │
│  - MinIO task spec read/write                                        │
│  - Worker lifecycle management                                        │
│  - hiclaw_create_worker tool (spawns containers via Docker proxy)     │
└──────────────────────────────────────────────────────────────────────┘
```

### hiclaw Worker Types (for context)

| Type | Protocol | Config Storage | Used By |
|------|---------|---------------|---------|
| **OpenClaw** | Matrix | `agents/${name}/openclaw.json` in MinIO | OpenClaw workers |
| **CoPaw** | HTTP/MinIO | `HICLAW_FS_ENDPOINT` (Higress FS gateway) | CoPaw workers |

hermes-agent workers are **OpenClaw-compatible**: they use Matrix for worker↔manager communication and read/write config via MinIO — the same protocol as OpenClaw workers.

---

## Network Topology (hiclaw-docker-proxy network: `hiclaw-net`)

| Hostname | Resolves To | Notes |
|----------|-------------|-------|
| `hiclaw-manager` | `172.21.0.3` | Manager container — hosts MinIO (9000) + Tuwunel/Matrix API (6167) |
| `minio` | **FAILS** | Not registered in Docker's internal DNS |
| `fs-local.hiclaw.io` | `10.66.66.1` (wrong) | Resolves via host systemd-resolved, not hiclaw-net |
| `172.21.0.3` | `172.21.0.3` | Works — use for direct MinIO/Matrix connections |

---

## Repository & Images

| Item | Location |
|------|---------|
| Fork | `https://github.com/TotalLag/hermes-agent` (`main` branch) |
| GHCR (read) | `ghcr.io/totallag/hermes-worker:latest` |
| hiclaw Manager env | `/home/master/hiclaw-manager.env` |

### Git History (most recent first)

```
9789fda - fix(hiclaw): Fix MinIO hostname and Matrix internal URL defaults
5c8ba71 - fix(hiclaw): Worker connectivity fixes - MinIO host and Matrix SSL
ddc43fb - fix(hiclaw): Fix worker entrypoint and create_worker tool
4357135 - fix(hiclaw): Fix pull_config syntax and use agents/{name}/openclaw.json path
8fa641a - fix(hiclaw): Dockerfile.worker COPY path correction
0fce8b6 - ci: Add hermes-worker image build and push workflow
4bb6636 - feat(hiclaw): Add hiclaw_create_worker tool for spawning hermes workers
```

---

## hiclaw Manager Configuration (from `/home/master/hiclaw-manager.env`)

```
HICLAW_MATRIX_DOMAIN=matrix-local.hiclaw.io:18080
HICLAW_REGISTRATION_TOKEN=<token>           # Workers use this to register
HICLAW_MANAGER_ROOM_ID=!ON8EQq2K6R1CJeECGD:matrix-local.hiclaw.io:18080
HICLAW_STORAGE_BUCKET=hiclaw-storage
HICLAW_STORAGE_PREFIX=agents
HICLAW_MINIO_USER=root
HICLAW_MINIO_PASSWORD=Welcome123!
HICLAW_BUCKET=hiclaw-storage
HICLAW_WORKER_IMAGE=...                     # Override to point at hermes-worker image
HICLAW_DEFAULT_WORKER_RUNTIME=copaw        # Set to openclaw or copaw
HICLAW_DOCKER_PROXY=1                      # Docker proxy API for spawning workers
```

---

## Environment Variables (hermes-worker container)

### Required

| Variable | Description |
|----------|-------------|
| `HICLAW_WORKER_NAME` | Unique worker name (becomes part of MinIO path) |
| `HICLAW_MATRIX_HOMESERVER` | External HTTPS Matrix URL (e.g., `https://matrix-local.hiclaw.io:18080`) — used by nio Matrix sync client |
| `HICLAW_MATRIX_INTERNAL_URL` | Internal HTTP Matrix/Tuwunel URL (e.g., `http://hiclaw-manager:6167`) — used for worker registration HTTP calls |
| `HICLAW_MATRIX_USER_ID` | Matrix user ID for this worker (e.g., `@my-worker:matrix-local.hiclaw.io:18080`) |
| `HICLAW_MANAGER_ROOM_ID` | Matrix room ID for Manager communication |
| `HICLAW_REGISTRATION_TOKEN` | Token for Matrix worker registration |
| `HICLAW_MATRIX_DOMAIN` | Matrix domain string |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `HICLAW_MINIO_HOST` | `http://hiclaw-manager:9000` | MinIO endpoint (use `http://hiclaw-manager:9000` inside containers) |
| `HICLAW_ACCESS_KEY` | MinIO access key | |
| `HICLAW_SECRET_KEY` | MinIO secret key | |
| `HICLAW_BUCKET` | `hiclaw-storage` | MinIO bucket name |
| `HICLAW_STORAGE_BUCKET` | same as `HICLAW_BUCKET` | |
| `HICLAW_STORAGE_PREFIX` | `agents` | Path prefix in MinIO |
| `HICLAW_TASK_SPECS_PREFIX` | `task-specs/` | Task specs prefix |
| `HICLAW_TASK_RESULTS_PREFIX` | `task-results/` | Task results prefix |
| `HICLAW_MATRIX_DEVICE_ID` | `HERMES01` | Matrix device ID |
| `HICLAW_MATRIX_ACCESS_TOKEN` | (empty) | Not needed when using registration flow |
| `HICLAW_HERMES_MODE` | `cli` | Must be `gateway` for hiclaw worker mode |
| `HICLAW_MAX_RETRIES` | `10` | Entrypoint max retries |
| `HICLAW_HERMES_GATEWAY_CMD` | `hermes gateway run` | Gateway launch command |
| `HICLAW_CONFIG_PATH` | `/app/hiclaw/config.yaml` | Output of config transform |
| `HICLAW_OPENCLAW_PATH` | `/app/hiclaw/openclaw.json` | Input to config transform |

---

## Startup Sequence (hermes-entrypoint.sh)

```
1. configure_matrix_session()
   → Exports MATRIX_HOMESERVER, MATRIX_USER_ID, MATRIX_DEVICE_ID

2. pull_config() [up to HICLAW_MAX_RETRIES retries with exponential backoff]
   → mc alias set hiclaw <HICLAW_MINIO_HOST>
   → mc cp -r hiclaw/<bucket>/agents/<worker_name>/openclaw.json /app/hiclaw/scripts/
   → Warns if not found (uses defaults)

3. transform_config() [exit on failure]
   → python hiclaw_config_transform.py openclaw.json → config.yaml

4. register_worker()
   → bash create-hermes-worker.sh register
   → Sends registration + ready status to HICLAW_MANAGER_ROOM_ID via Matrix HTTP API

5. launch_gateway()
   → hermes gateway run
   → Gateway listens on Matrix, receives task assignments, executes via AIAgent
```

---

## Worker Registration (create-hermes-worker.sh)

Workers register with the Manager by sending a Matrix message to the Manager room:

```json
{
  "id": "<hostname>-<worker_name>",
  "name": "<worker_name>",
  "capabilities": ["task-execution", "file-sync", "hermes-agent"],
  "status": "registered",
  "version": "1.0.0",
  "matrix_user_id": "<HICLAW_MATRIX_USER_ID>",
  "device_id": "<HICLAW_MATRIX_DEVICE_ID>"
}
```

Then send a status update:
```json
{"status": "ready", "worker": "<worker_name>", "timestamp": "..."}
```

Matrix HTTP API calls go to `HICLAW_MATRIX_INTERNAL_URL` (HTTP, internal) — NOT `HICLAW_MATRIX_HOMESERVER` (HTTPS, external). This avoids SSL cert issues on the internal Docker network.

---

## Config Transform (hiclaw_config_transform.py)

The worker reads `openclaw.json` from MinIO and transforms it to Hermes `config.yaml` at startup.

**Supported openclaw.json fields → Hermes config.yaml:**

| openclaw.json | config.yaml |
|---------------|-------------|
| `model` | `models.default.id` + `models.default.provider` |
| `provider` | provider override |
| `channels` | `channels` |
| `toolsets` | `tools` |
| `skills` | `skills` |
| `memory` | `memory` |
| `mcpServers` | `mcpServers` |
| `aiGateway` | `providers.aiGateway` |

Unknown fields are logged as warnings and skipped (no error).

---

## Spawning Workers via `hiclaw_create_worker` Tool

The Manager has a `hiclaw_create_worker` tool (defined in `tools/hiclaw_manager_tool.py`) that spawns hermes-worker containers via the Docker proxy API.

**Flow:**
1. Tool receives `worker_name`, `worker_type=hermes`
2. Registers worker with Matrix (obtains access token via registration token)
3. Calls Docker proxy API (`POST /containers/create`) on `hiclaw-net`
4. Container starts with all required env vars including `HICLAW_MINIO_HOST=http://hiclaw-manager:9000` and `HICLAW_MATRIX_INTERNAL_URL=http://hiclaw-manager:6167`
5. Worker container pulls its config, transforms, registers, and starts gateway

**Container env vars set by the tool:**

```
HICLAW_WORKER_NAME
HICLAW_MATRIX_HOMESERVER=https://<domain>
HICLAW_MATRIX_INTERNAL_URL=http://hiclaw-manager:6167
HICLAW_MATRIX_USER_ID
HICLAW_MATRIX_ACCESS_TOKEN
HICLAW_MATRIX_DEVICE_ID
HICLAW_MANAGER_ROOM_ID
HICLAW_MC_HOST
HICLAW_BUCKET
HICLAW_ACCESS_KEY
HICLAW_SECRET_KEY
HICLAW_STORAGE_BUCKET
HICLAW_STORAGE_PREFIX
HICLAW_TASK_SPECS_PREFIX
HICLAW_TASK_RESULTS_PREFIX
HICLAW_MINIO_HOST=http://hiclaw-manager:9000
HICLAW_HERMES_MODE=gateway
HERMES_WORKER_IMAGE
```

**Docker network:** `hiclaw-net` (same network as Manager and MinIO)

---

## Docker Proxy Allowlist

The hiclaw Docker proxy only allows images from:
- `localhost`, `local` (local build/test)
- Higress registry (`higress-registry.us-west-1.cr.aliyuncs.com/...`)
- Configured registries

**`ghcr.io/totallag/hermes-worker:latest` is blocked** by the proxy's allowlist. To use GHCR images:

1. Build locally: `docker build -f Dockerfile.worker -t localhost/hermes-worker:latest ..`
2. Push to a configured registry (add to `HICLAW_PROXY_ALLOWED_REGISTRIES`)
3. Or use the workflow to push from GitHub Actions (which runs outside the proxy's network)

---

## Development

### Build Worker Container (local)

```bash
cd hermes-agent/hiclaw
docker build -f Dockerfile.worker -t hermes-worker:latest ..
docker build -f Dockerfile.worker -t localhost/hermes-worker:latest ..
```

### Build with Custom Hermes Fork

```bash
docker build \
  --build-arg HERMES_AGENT_GIT_URL=https://github.com/YourFork/hermes-agent \
  --build-arg HERMES_AGENT_REF=your-branch \
  -f Dockerfile.worker \
  -t hermes-worker:custom \
  ..
```

### Test Config Transform Locally

```bash
python hermes-agent/hiclaw/scripts/hiclaw_config_transform.py \
  /path/to/openclaw.json /tmp/config.yaml
```

### Check Worker Logs

```bash
docker logs <worker_container_name>
docker exec <worker_container_name> cat /app/logs/startup.log
```

### Verify MinIO Connectivity from Worker

```bash
docker exec <worker_container_name> mc alias list
docker exec <worker_container_name> mc ls hiclaw/hiclaw-storage/agents/
```

### Test Matrix Registration from Worker

```bash
docker exec <worker_container_name> bash -c '
  curl -sf -X PUT \
    -H "Authorization: Bearer ${HICLAW_REGISTRATION_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"msgtype\": \"m.text\", \"content\": {\"body\": \"test\"}}" \
    "${HICLAW_MATRIX_INTERNAL_URL}/_matrix/client/r0/rooms/${HICLAW_MANAGER_ROOM_ID}/send/m.room.message/test123"
'
```

---

## Troubleshooting

### Container exits immediately

```bash
docker logs <container>
docker exec <container cat /app/logs/startup.log
```

### `mc: Unable to prepare URL for copying. dial tcp: lookup minio on 127.0.0.11:53: server misbehaving`

`minio` hostname doesn't resolve inside worker containers. Use `HICLAW_MINIO_HOST=http://hiclaw-manager:9000` (or the direct IP). This is the default in the entrypoint — ensure `HICLAW_MINIO_HOST` is set correctly.

### `Failed to send matrix message` during registration

`HICLAW_MATRIX_INTERNAL_URL` is probably `http://127.0.0.1:6167` (loopback). Inside a container, `127.0.0.1` refers to the container itself, not the Manager. Set it to `http://hiclaw-manager:6167`.

### Config not found in MinIO

Path is `agents/<worker_name>/openclaw.json` — not at the bucket root. Ensure the worker name matches the MinIO path structure.

### Image blocked by Docker proxy

The proxy only allows `localhost`, `local`, Higress registry, or configured registries. Build locally and tag as `localhost/hermes-worker:latest`.

### Gateway exits immediately

Check that `HICLAW_HERMES_MODE=gateway` is set. The default is `cli` which starts an interactive terminal (not suitable for workers).

---

## Production Checklist

- [ ] Worker image is in an allowlisted registry OR built locally and tagged `localhost/hermes-worker:latest`
- [ ] `HICLAW_MINIO_HOST=http://hiclaw-manager:9000` is set (or `HICLAW_MC_HOST` for the spawn tool)
- [ ] `HICLAW_MATRIX_INTERNAL_URL=http://hiclaw-manager:6167` is set (not `127.0.0.1`)
- [ ] `HICLAW_MATRIX_HOMESERVER=https://...` is the external HTTPS URL for nio Matrix sync
- [ ] `HICLAW_HERMES_MODE=gateway` is set
- [ ] `HICLAW_REGISTRATION_TOKEN` matches the Manager's `HICLAW_REGISTRATION_TOKEN`
- [ ] Worker config exists at `agents/<worker_name>/openclaw.json` in MinIO bucket
- [ ] Docker proxy is network-accessible from the Manager container
