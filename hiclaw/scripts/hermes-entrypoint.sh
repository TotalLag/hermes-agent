#!/bin/bash
# =============================================================================
# hermes-entrypoint.sh - Container Startup for Hermes hiclaw Worker
# =============================================================================

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

pull_config() {
    if [[ -z "${HICLAW_MC_HOST:-}" ]] || [[ -z "${HICLAW_BUCKET:-}" ]]; then
        log "WARN: MinIO not configured, skipping config pull"
        return 0
    fi
    
    log "Pulling config from MinIO..."
    
    if source "${SCRIPT_DIR}/hiclaw-sync.sh" && hiclaw-sync.sh pull openclaw.json "${SCRIPT_DIR}/"; then
        log "Config pulled successfully"
    else
        log_error "Failed to pull config from MinIO"
        return 1
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

matrix_login() {
    if [[ -z "${HICLAW_MATRIX_ACCESS_TOKEN:-}" ]]; then
        log "WARN: No Matrix access token, skipping Matrix login"
        return 0
    fi
    
    log "Matrix access token available"
}

launch_hermes() {
    local mode="${HICLAW_HERMES_MODE:-cli}"
    local config_path="${HICLAW_CONFIG_PATH:-${SCRIPT_DIR}/config.yaml}"
    
    log "Launching Hermes in ${mode} mode..."
    
    case "${mode}" in
        gateway)
            local gateway_cmd="${HICLAW_HERMES_GATEWAY_CMD:-hermes gateway start}"
            log "Starting gateway: ${gateway_cmd}"
            eval "${gateway_cmd}"
            ;;
        cli|*)
            local cli_cmd="${HICLAW_HERMES_CLI_CMD:-hermes run}"
            log "Starting CLI: ${cli_cmd}"
            eval "${cli_cmd}"
            ;;
    esac
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
    
    local max_retries="${HICLAW_MAX_RETRIES:-5}"
    local attempt=0
    
    while [[ ${attempt} -lt ${max_retries} ]]; do
        if pull_config && transform_config && matrix_login; then
            if launch_hermes; then
                log "Hermes exited normally"
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
