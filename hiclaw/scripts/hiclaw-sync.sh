#!/bin/bash
# =============================================================================
# hiclaw-sync.sh - MinIO File Sync for Hermes Worker
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/hiclaw_sync_lib.sh" 2>/dev/null || true

HICLAW_STATE_DIR="${HOME}/.hermes/hiclaw"
TASK_SPECS_DIR="${HICLAW_STATE_DIR}/task-specs"
TASK_RESULTS_DIR="${HICLAW_STATE_DIR}/task-results"

usage() {
    cat <<EOF
Usage: hiclaw-sync.sh <command> [options]

Commands:
    push <local_path> <remote_path>   Upload local file/directory to MinIO
    pull <remote_path> [local_path]   Download remote file/directory from MinIO
    sync <local_path> <remote_path>   Bidirectional sync
    watch <local_path> <remote_path>  Continuous sync with change detection

    pull-specs                       Pull task specs from MinIO to local specs dir
    push-results                      Push task results from local to MinIO
    sync-all                          Bidirectional sync for both specs and results
    status                            Show sync status for specs and results

Environment Variables:
    HICLAW_MC_HOST     MinIO/S3 endpoint URL
    HICLAW_BUCKET      MinIO bucket name
    HICLAW_ACCESS_KEY  Access key (or use MC_HOST_hiclaw alias)
    HICLAW_SECRET_KEY  Secret key
    HICLAW_TASK_SPECS_PREFIX  Remote prefix for task specs (default: task-specs)
    HICLAW_TASK_RESULTS_PREFIX  Remote prefix for task results (default: task-results)

Local Directories:
    Task specs:  ${TASK_SPECS_DIR}
    Task results: ${TASK_RESULTS_DIR}

Examples:
    hiclaw-sync.sh pull openclaw.json /app/config/
    HICLAW_BUCKET=workers hiclaw-sync.sh push /app/data/ results/
    hiclaw-sync.sh pull-specs
    hiclaw-sync.sh push-results
    hiclaw-sync.sh sync-all
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

cmd_pull_specs() {
    configure_mc
    
    if [[ -z "${HICLAW_BUCKET:-}" ]]; then
        log "ERROR: HICLAW_BUCKET not set"
        exit 1
    fi
    
    local specs_prefix="${HICLAW_TASK_SPECS_PREFIX:-task-specs}"
    mkdir -p "${TASK_SPECS_DIR}"
    
    log "Pulling task specs from ${HICLAW_BUCKET}/${specs_prefix}/ to ${TASK_SPECS_DIR}/"
    mc cp --recursive "hiclaw/${HICLAW_BUCKET}/${specs_prefix}/" "${TASK_SPECS_DIR}/" 2>/dev/null || true
    log "Pull specs complete"
}

cmd_push_results() {
    configure_mc
    
    if [[ -z "${HICLAW_BUCKET:-}" ]]; then
        log "ERROR: HICLAW_BUCKET not set"
        exit 1
    fi
    
    local results_prefix="${HICLAW_TASK_RESULTS_PREFIX:-task-results}"
    mkdir -p "${TASK_RESULTS_DIR}"
    
    log "Pushing task results from ${TASK_RESULTS_DIR}/ to ${HICLAW_BUCKET}/${results_prefix}/"
    mc cp --recursive --overwrite "${TASK_RESULTS_DIR}/" "hiclaw/${HICLAW_BUCKET}/${results_prefix}/"
    log "Push results complete"
}

cmd_sync_all() {
    configure_mc
    
    if [[ -z "${HICLAW_BUCKET:-}" ]]; then
        log "ERROR: HICLAW_BUCKET not set"
        exit 1
    fi
    
    local specs_prefix="${HICLAW_TASK_SPECS_PREFIX:-task-specs}"
    local results_prefix="${HICLAW_TASK_RESULTS_PREFIX:-task-results}"
    
    mkdir -p "${TASK_SPECS_DIR}" "${TASK_RESULTS_DIR}"
    
    log "Syncing task specs: ${TASK_SPECS_DIR}/ <-> ${HICLAW_BUCKET}/${specs_prefix}/"
    mc mirror --overwrite "${TASK_SPECS_DIR}/" "hiclaw/${HICLAW_BUCKET}/${specs_prefix}/"
    
    log "Syncing task results: ${TASK_RESULTS_DIR}/ <-> ${HICLAW_BUCKET}/${results_prefix}/"
    mc mirror --overwrite "${TASK_RESULTS_DIR}/" "hiclaw/${HICLAW_BUCKET}/${results_prefix}/"
    
    log "Sync-all complete"
}

cmd_sync_status() {
    local specs_count=0
    local results_count=0
    local specs_remote_count=0
    local results_remote_count=0
    
    if [[ -d "${TASK_SPECS_DIR}" ]]; then
        specs_count=$(find "${TASK_SPECS_DIR}" -type f 2>/dev/null | wc -l)
    fi
    
    if [[ -d "${TASK_RESULTS_DIR}" ]]; then
        results_count=$(find "${TASK_RESULTS_DIR}" -type f 2>/dev/null | wc -l)
    fi
    
    configure_mc
    
    if [[ -n "${HICLAW_BUCKET:-}" ]]; then
        local specs_prefix="${HICLAW_TASK_SPECS_PREFIX:-task-specs}"
        local results_prefix="${HICLAW_TASK_RESULTS_PREFIX:-task-results}"
        
        specs_remote_count=$(mc ls --recursive "hiclaw/${HICLAW_BUCKET}/${specs_prefix}/" 2>/dev/null | wc -l || echo "0")
        results_remote_count=$(mc ls --recursive "hiclaw/${HICLAW_BUCKET}/${results_prefix}/" 2>/dev/null | wc -l || echo "0")
    fi
    
    echo "Task Specs:"
    echo "  Local:  ${specs_count} files in ${TASK_SPECS_DIR}"
    echo "  Remote: ${specs_remote_count} objects in ${HICLAW_BUCKET:-<not configured>}/${HICLAW_TASK_SPECS_PREFIX:-task-specs}/"
    echo ""
    echo "Task Results:"
    echo "  Local:  ${results_count} files in ${TASK_RESULTS_DIR}"
    echo "  Remote: ${results_remote_count} objects in ${HICLAW_BUCKET:-<not configured>}/${HICLAW_TASK_RESULTS_PREFIX:-task-results}/"
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
        pull-specs) cmd_pull_specs; ;;
        push-results) cmd_push_results; ;;
        sync-all) cmd_sync_all; ;;
        status) cmd_sync_status; ;;
        help|--help|-h) usage; ;;
        *)
            log "ERROR: Unknown command: ${command}"
            usage
            exit 1
            ;;
    esac
}

main "$@"
