#!/bin/bash
set -e

log() { echo "[$(date -Iseconds)] hermes-entrypoint: $1"; }

# Handle supervisor exec mode
if [ "$1" = "exec" ]; then
    shift
    exec "$@"
fi

# Set timezone from TZ env var
if [ -n "$TZ" ]; then
    ln -snf /usr/share/zoneinfo/"$TZ" /etc/localtime 2>/dev/null || true
fi

# Source hiclaw environment
if [ -f /opt/hiclaw/scripts/lib/hiclaw-env.sh ]; then
    . /opt/hiclaw/scripts/lib/hiclaw-env.sh
fi

# Wait for infrastructure service to be available
wait_for_port() {
    local host="$1" port="$2" name="$3" timeout="${4:-120}"
    local elapsed=0
    echo "[hermes-entrypoint] Waiting for ${name} at ${host}:${port}..."
    while ! nc -z "${host}" "${port}" 2>/dev/null; do
        sleep 2
        elapsed=$((elapsed + 2))
        if [ "${elapsed}" -ge "${timeout}" ]; then
            echo "[hermes-entrypoint] ERROR: ${name} did not become available within ${timeout}s"
            return 1
        fi
        echo "[hermes-entrypoint] Still waiting for ${name}... (${elapsed}s elapsed)"
    done
    echo "[hermes-entrypoint] ${name} is ready"
}

# Wait for all infrastructure services
log "Waiting for infrastructure services..."

# MinIO - S3-compatible object storage
wait_for_port "${HICLAW_MINIO_HOST:-minio}" 9000 "MinIO" 120

# Tuwunel - tunnel service
wait_for_port "${HICLAW_TUWUNEL_HOST:-tuwunel}" 6167 "Tuwunel" 120

# Higress Gateway - API gateway
wait_for_port "${HICLAW_HIGRESS_HOST:-higress-gateway}" 8080 "Higress Gateway" 120

# Higress Console - Higress admin UI
wait_for_port "${HICLAW_HIGRESS_HOST:-higress-gateway}" 8001 "Higress Console" 120

log "All infrastructure services are ready"

# Set up workspace
WORKSPACE_DIR="${HICLAW_WORKSPACE_DIR:-/root/manager-workspace}"
mkdir -p "$WORKSPACE_DIR"
mkdir -p "$WORKSPACE_DIR/.hermes"

log "Starting hermes-agent hiclaw Manager..."

log "Calling init_manager() for infrastructure setup..."
python3 -c "
import sys
import os
sys.path.insert(0, '/opt/hermes-source')
os.environ['HERMES_HOME'] = '${WORKSPACE_DIR}/.hermes'
from gateway.hiclaw.manager_init import init_manager
result = init_manager('${WORKSPACE_DIR}')
if not result.get('success'):
    print('init_manager failed:', result.get('error', 'unknown error'), file=sys.stderr)
    sys.exit(1)
print('init_manager completed:', result.get('config_path'))
"

log "hermes-agent Manager init complete. Starting hermes..."

exec hermes gateway start --config "$WORKSPACE_DIR/hermes-config.yaml"
