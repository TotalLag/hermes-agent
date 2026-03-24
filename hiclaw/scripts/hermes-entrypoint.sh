#!/bin/bash
# hermes-entrypoint.sh - Container Startup for Hermes hiclaw Worker

set -euo pipefail

SCRIPT_DIR="/app/hiclaw/scripts"
LOG_DIR="/app/logs"
mkdir -p "${LOG_DIR}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ENTRYPOINT] $*"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ENTRYPOINT] $*" >> "${LOG_DIR}/startup.log"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ENTRYPOINT] ERROR: $*" >&2
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ENTRYPOINT] ERROR: $*" >> "${LOG_DIR}/startup.log"
}

configure_matrix_session() {
    local session_dir="${HOME}/.hermes"
    mkdir -p "${session_dir}"
    
    if [[ -n "${HICLAW_MATRIX_HOMESERVER:-}" ]]; then
        export MATRIX_HOMESERVER="${HICLAW_MATRIX_HOMESERVER}"
    fi
    if [[ -n "${HICLAW_MATRIX_USER_ID:-}" ]]; then
        export MATRIX_USER_ID="${HICLAW_MATRIX_USER_ID}"
    fi
    if [[ -n "${HICLAW_MATRIX_DEVICE_ID:-}" ]]; then
        export MATRIX_DEVICE_ID="${HICLAW_MATRIX_DEVICE_ID}"
    fi
    
    log "Matrix session configured"
}

register_worker() {
    if [[ -z "${HICLAW_MANAGER_ROOM_ID:-}" ]]; then
        log "WARN: HICLAW_MANAGER_ROOM_ID not set, skipping registration"
        return 0
    fi
    
    log "Registering worker with Manager..."
    
    if bash "${SCRIPT_DIR}/create-hermes-worker.sh" register; then
        log "Worker registered successfully"
    else
        log_error "Worker registration failed"
        return 1
    fi
}

pull_config() {
    local bucket="${HICLAW_BUCKET:-hiclaw-storage}"
    local prefix="${HICLAW_STORAGE_PREFIX:-agents}"
    
    if [[ -z "${HICLAW_ACCESS_KEY:-}" ]] || [[ -z "${HICLAW_SECRET_KEY:-}" ]]; then
        log "WARN: MinIO credentials not configured, skipping config pull"
        return 0
    fi
    
    local remote_path="openclaw.json"
    if [[ -n "${HICLAW_WORKER_NAME:-}" ]]; then
        remote_path="agents/${HICLAW_WORKER_NAME}/openclaw.json"
    fi
    
    log "Pulling config from MinIO (${remote_path})..."
    
    local minio_host="${HICLAW_MINIO_HOST:-http://hiclaw-manager:9000}"
    minio_host="${minio_host#http://}"
    minio_host="${minio_host#https://}"
    mc alias set hiclaw "http://${minio_host}" "${HICLAW_ACCESS_KEY}" "${HICLAW_SECRET_KEY}" 2>/dev/null || true
    
    if mc cp -r "hiclaw/${bucket}/${remote_path}" "${SCRIPT_DIR}/"; then
        log "Config pulled successfully"
    else
        log "WARN: Config not found in MinIO (${remote_path}), using defaults"
    fi
}

transform_config() {
    local input_json="${SCRIPT_DIR}/openclaw.json"
    local output_yaml="${SCRIPT_DIR}/config.yaml"
    
    if [[ ! -f "${input_json}" ]]; then
        log "WARN: No openclaw.json found, using default config"
        return 0
    fi
    
    log "Transforming config..."
    
    if python "${SCRIPT_DIR}/hiclaw_config_transform.py" "${input_json}" "${output_yaml}"; then
        log "Config transformed successfully"
        export HICLAW_CONFIG_PATH="${output_yaml}"
    else
        log_error "Config transformation failed"
        return 1
    fi
}

launch_gateway() {
    local gateway_cmd="${HICLAW_HERMES_GATEWAY_CMD:-hermes gateway run}"
    
    log "Starting gateway: ${gateway_cmd}"
    log "Worker will receive task assignments via Matrix and execute them using AIAgent"
    
    eval "${gateway_cmd}"
}

exponential_backoff() {
    local attempt="$1"
    local max_delay=300
    local delay=$((2 ** attempt))
    [[ ${delay} -gt ${max_delay} ]] && delay=${max_delay}
    log "Retry in ${delay}s (attempt $((attempt + 1)))"
    sleep "${delay}"
}

main() {
    log "=========================================="
    log "Hermes hiclaw Worker Starting"
    log "=========================================="
    
    configure_matrix_session
    
    local max_retries="${HICLAW_MAX_RETRIES:-10}"
    local attempt=0
    
    while [[ ${attempt} -lt ${max_retries} ]]; do
        if pull_config && transform_config && register_worker; then
            if launch_gateway; then
                log "Gateway exited normally"
                exit 0
            fi
        fi
        
        attempt=$((attempt + 1))
        if [[ ${attempt} -lt ${max_retries} ]]; then
            exponential_backoff ${attempt}
        fi
    done
    
    log_error "Max retries (${max_retries}) exceeded, exiting"
    exit 1
}

main "$@"
