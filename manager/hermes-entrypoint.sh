#!/bin/bash
# hermes-entrypoint.sh — Manager container entrypoint
# Replaces start-manager-agent.sh from the OpenClaw hiclaw Manager.
# Handles init sequence then launches hermes-agent.

set -e

log() { echo "[$(date -Iseconds)] hermes-entrypoint: $1"; }

# If "exec" arg passed, just exec the final command (supervisord mode)
if [ "$1" = "exec" ]; then
    shift
    exec "$@"
fi

# Source hiclaw env library if available
if [ -f /opt/hiclaw/scripts/lib/hiclaw-env.sh ]; then
    . /opt/hiclaw/scripts/lib/hiclaw-env.sh
fi

# Timezone
ln -snf /usr/share/zoneinfo/UTC /etc/localtime 2>/dev/null || true

# Workspace
WORKSPACE_DIR="${HICLAW_WORKSPACE_DIR:-/root/manager-workspace}"
mkdir -p "$WORKSPACE_DIR"
mkdir -p "$WORKSPACE_DIR/.hermes"

log "Starting hermes-agent hiclaw Manager..."

# Wait for infrastructure
log "Waiting for Higress (8080/8001)..."
waitForService 8080 30 || log "Higress gateway not ready (non-fatal)"
waitForService 8001 30 || log "Higress controller not ready (non-fatal)"

log "Waiting for Tuwunel (6167)..."
waitForService 6167 30 || log "Tuwunel not ready (non-fatal)"

log "Waiting for MinIO (9000)..."
waitForService 9000 30 || log "MinIO not ready (non-fatal)"

# Secrets
SECRETS_FILE="${HICLAW_DATA_DIR:-/data}/hiclaw-secrets.env"
mkdir -p "$(dirname "$SECRETS_FILE")"

if [ -z "$HICLAW_MANAGER_GATEWAY_KEY" ]; then
    log "Generating MANAGER_GATEWAY_KEY..."
    export HICLAW_MANAGER_GATEWAY_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
fi

if [ -z "$HICLAW_MANAGER_PASSWORD" ]; then
    log "Generating MANAGER_PASSWORD..."
    export HICLAW_MANAGER_PASSWORD=$(python3 -c "import secrets; print(secrets.token_hex(16))")
fi

cat > "$SECRETS_FILE" <<EOF
HICLAW_MANAGER_GATEWAY_KEY=${HICLAW_MANAGER_GATEWAY_KEY}
HICLAW_MANAGER_PASSWORD=${HICLAW_MANAGER_PASSWORD}
EOF

# Source secrets
. "$SECRETS_FILE"

# Init Matrix accounts (idempotent)
MATRIX_AUTH_DIR="$WORKSPACE_DIR/.hermes"
mkdir -p "$MATRIX_AUTH_DIR"

if [ ! -f "$MATRIX_AUTH_DIR/matrix-token" ]; then
    log "Registering Matrix manager bot account..."
    
    MANAGER_USER="manager_$(echo $HICLAW_MANAGER_GATEWAY_KEY | head -c 8)"
    
    TOKEN_RESPONSE=$(curl -s -X POST "http://matrix-local.hiclaw.io:18080/_matrix/client/v3/register" \
        -H "Content-Type: application/json" \
        -d "{\"auth\": {\"session\": \"\", \"type\": \"m.login.dummy\"}, \"username\": \"$MANAGER_USER\", \"password\": \"$HICLAW_MANAGER_PASSWORD\"}" || echo "{}")
    
    MANAGER_TOKEN=$(echo "$TOKEN_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null || echo "")
    
    if [ -n "$MANAGER_TOKEN" ]; then
        echo "{\"access_token\": \"$MANAGER_TOKEN\"}" > "$MATRIX_AUTH_DIR/matrix-token"
        log "Matrix manager bot registered successfully."
    else
        log "Matrix registration failed or already exists (non-fatal)."
        log "Token response: $TOKEN_RESPONSE"
    fi
fi

# Init Higress Console (idempotent)
if [ -n "$HICLAW_ADMIN_USER" ] && [ -n "$HICLAW_ADMIN_PASSWORD" ]; then
    log "Initializing Higress Console..."
    curl -s -X POST "http://hiclaw-local.hiclaw.io:8001/apis/configs/v1/admingateway" \
        -H "Content-Type: application/json" \
        -d "{\"username\": \"$HICLAW_ADMIN_USER\", \"password\": \"$HICLAW_ADMIN_PASSWORD\"}" \
        || log "Higress admin init failed (may already exist)"
fi

# Generate hermes Manager config
log "Generating hermes Manager config..."
cat > "$WORKSPACE_DIR/hermes-config.yaml" <<HERMESCFG
version: "1"

hiclaw:
  manager:
    gateway_key: "${HICLAW_MANAGER_GATEWAY_KEY}"
    password: "${HICLAW_MANAGER_PASSWORD}"
    workspace: "${WORKSPACE_DIR}"

  infrastructure:
    matrix_domain: "${HICLAW_MATRIX_DOMAIN}"
    docker_proxy: "${HICLAW_DOCKER_PROXY:-http://hiclaw-docker-proxy:2375}"
    minio_bucket: "${HICLAW_MINIO_BUCKET:-hiclaw}"
    minio_prefix_tasks: "${HICLAW_TASK_SPECS_PREFIX:-task-specs/}"
    minio_prefix_results: "${HICLAW_TASK_RESULTS_PREFIX:-task-results/}"

  llm:
    provider: "${HICLAW_LLM_PROVIDER:-openai-compat}"
    base_url: "${HICLAW_OPENAI_BASE_URL}"
    model: "${HICLAW_DEFAULT_MODEL:-MiniMax-M2.7}"
    api_key: "${HICLAW_LLM_API_KEY}"

platforms:
  matrix:
    homeserver: "http://${HICLAW_MATRIX_DOMAIN}"
    access_token_env: "HERMES_MATRIX_TOKEN"
    require_mention: true
    encryption: false
HERMESCFG

# Set Matrix token env var from file
if [ -f "$MATRIX_AUTH_DIR/matrix-token" ]; then
    export HERMES_MATRIX_TOKEN=$(python3 -c "import json; print(json.load(open('$MATRIX_AUTH_DIR/matrix-token'))['access_token'])")
fi

# MinIO client config
if [ -n "$HICLAW_MINIO_USER" ] && [ -n "$HICLAW_MINIO_PASSWORD" ]; then
    log "Configuring MinIO client..."
    mc alias set myminio http://minio:9000 "$HICLAW_MINIO_USER" "$HICLAW_MINIO_PASSWORD" 2>/dev/null || \
    mc alias set myminio http://localhost:9000 "$HICLAW_MINIO_USER" "$HICLAW_MINIO_PASSWORD" 2>/dev/null || true
fi

log "hermes-agent Manager init complete. Starting hermes..."

# Start hermes-agent Manager
# The supervisor will keep this running
exec hermes gateway start --config "$WORKSPACE_DIR/hermes-config.yaml"
