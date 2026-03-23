#!/bin/bash
# =============================================================================
# hiclaw-sync.sh - MinIO File Sync for Hermes Worker
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/hiclaw_sync_lib.sh" 2>/dev/null || true

usage() {
    cat <<EOF
Usage: hiclaw-sync.sh <command> [options]

Commands:
    push <local_path> <remote_path>   Upload local file/directory to MinIO
    pull <remote_path> [local_path]   Download remote file/directory from MinIO
    sync <local_path> <remote_path>   Bidirectional sync
    watch <local_path> <remote_path>  Continuous sync with change detection

Environment Variables:
    HICLAW_MC_HOST     MinIO/S3 endpoint URL
    HICLAW_BUCKET      MinIO bucket name
    HICLAW_ACCESS_KEY  Access key (or use MC_HOST_hiclaw alias)
    HICLAW_SECRET_KEY  Secret key

Examples:
    hiclaw-sync.sh pull openclaw.json /app/config/
    HICLAW_BUCKET=workers hiclaw-sync.sh push /app/data/ results/
EOF
}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

configure_mc() {
    if [[ -z "${HICLAW_MC_HOST:-}" ]]; then
        log "ERROR: HICLAW_MC_HOST not set"
        exit 1
    fi
    
    export MC_HOST_hiclaw="${HICLAW_MC_HOST}"
    
    if [[ -n "${HICLAW_ACCESS_KEY:-}" ]] && [[ -n "${HICLAW_SECRET_KEY:-}" ]]; then
        mc alias set hiclaw "${HICLAW_MC_HOST}" "${HICLAW_ACCESS_KEY}" "${HICLAW_SECRET_KEY}" 2>/dev/null || true
    fi
}

is_sensitive() {
    local path="$1"
    [[ "${path}" =~ \.env$ ]] || [[ "${path}" =~ \.key$ ]] || [[ "${path}" =~ \.pem$ ]]
}

cmd_push() {
    local local_path="$1"
    local remote_path="${2:-}"
    
    configure_mc
    
    if [[ -z "${HICLAW_BUCKET:-}" ]]; then
        log "ERROR: HICLAW_BUCKET not set"
        exit 1
    fi
    
    if [[ ! -e "${local_path}" ]]; then
        log "ERROR: Local path does not exist: ${local_path}"
        exit 1
    fi
    
    if is_sensitive "${local_path}"; then
        log "WARN: Skipping sensitive file: ${local_path}"
        exit 0
    fi
    
    log "Pushing ${local_path} to ${HICLAW_BUCKET}/${remote_path}"
    mc cp --overwrite "${local_path}" "hiclaw/${HICLAW_BUCKET}/${remote_path}"
    log "Push complete"
}

cmd_pull() {
    local remote_path="$1"
    local local_path="${2:-.}"
    
    configure_mc
    
    if [[ -z "${HICLAW_BUCKET:-}" ]]; then
        log "ERROR: HICLAW_BUCKET not set"
        exit 1
    fi
    
    log "Pulling ${HICLAW_BUCKET}/${remote_path} to ${local_path}"
    mc cp --overwrite "hiclaw/${HICLAW_BUCKET}/${remote_path}" "${local_path}"
    log "Pull complete"
}

cmd_sync() {
    local local_path="$1"
    local remote_path="$2"
    
    configure_mc
    
    if [[ -z "${HICLAW_BUCKET:-}" ]]; then
        log "ERROR: HICLAW_BUCKET not set"
        exit 1
    fi
    
    if [[ ! -d "${local_path}" ]]; then
        log "ERROR: Sync requires a local directory"
        exit 1
    fi
    
    log "Syncing ${local_path} <-> ${HICLAW_BUCKET}/${remote_path}"
    mc mirror --overwrite "${local_path}" "hiclaw/${HICLAW_BUCKET}/${remote_path}"
    log "Sync complete"
}

cmd_watch() {
    local local_path="$1"
    local remote_path="$2"
    
    configure_mc
    
    if [[ -z "${HICLAW_BUCKET:-}" ]]; then
        log "ERROR: HICLAW_BUCKET not set"
        exit 1
    fi
    
    log "Starting watch mode for ${local_path}"
    
    if command -v inotifywait &>/dev/null; then
        inotifywait -m -r -e modify,create,delete "${local_path}" 2>/dev/null | while read -r; do
            log "Change detected, syncing..."
            mc mirror --overwrite "${local_path}" "hiclaw/${HICLAW_BUCKET}/${remote_path}"
        done
    else
        log "WARN: inotifywait not available, using polling mode"
        while true; do
            sleep 30
            mc mirror --overwrite "${local_path}" "hiclaw/${HICLAW_BUCKET}/${remote_path}"
        done
    fi
}

main() {
    if [[ $# -lt 1 ]]; then
        usage
        exit 1
    fi
    
    local command="$1"
    shift
    
    case "${command}" in
        push)  cmd_push "$@"; ;;
        pull)  cmd_pull "$@"; ;;
        sync)  cmd_sync "$@"; ;;
        watch) cmd_watch "$@"; ;;
        help|--help|-h) usage; ;;
        *)
            log "ERROR: Unknown command: ${command}"
            usage
            exit 1
            ;;
    esac
}

main "$@"
