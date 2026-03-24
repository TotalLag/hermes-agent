#!/bin/bash
set -e

export HICLAW_MINIO_HOST="${HICLAW_MINIO_HOST:-127.0.0.1}"
export HICLAW_TUWUNEL_HOST="${HICLAW_TUWUNEL_HOST:-127.0.0.1}"
export HICLAW_HIGRESS_HOST="${HICLAW_HIGRESS_HOST:-127.0.0.1}"

if [ -n "$TZ" ]; then
    ln -snf /usr/share/zoneinfo/"$TZ" /etc/localtime 2>/dev/null || true
fi

source /opt/hiclaw/scripts/lib/hiclaw-env.sh 2>/dev/null || true

WORKSPACE_DIR="${HICLAW_WORKSPACE_DIR:-/root/manager-workspace}"
mkdir -p "$WORKSPACE_DIR"
mkdir -p "$WORKSPACE_DIR/.hermes"
export HERMES_HOME="${WORKSPACE_DIR}/.hermes"

if ! /opt/hermes-env/bin/python3 -c "
import sys
sys.path.insert(0, '/opt/hermes-source')
from gateway.hiclaw.manager_init import init_manager
result = init_manager('${WORKSPACE_DIR}')
if not result.get('success'):
    print('init_manager failed:', result.get('error', 'unknown error'), file=sys.stderr)
    sys.exit(1)
print('init_manager completed:', result.get('config_path'))
"; then
    echo "[run-gateway] init_manager failed, exiting"
    exit 1
fi

exec hermes gateway start --config "$WORKSPACE_DIR/hermes-config.yaml"
