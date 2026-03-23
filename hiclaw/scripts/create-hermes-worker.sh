#!/bin/bash
# =============================================================================
# create-hermes-worker.sh - Register Hermes Worker with hiclaw Manager
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WORKER] $*"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WORKER] ERROR: $*" >&2
}

check_env() {
    local missing=0
    for var in HICLAW_MATRIX_HOMESERVER HICLAW_MATRIX_ACCESS_TOKEN HICLAW_MATRIX_USER_ID HICLAW_MANAGER_ROOM_ID; do
        if [[ -z "${!var:-}" ]]; then
            log_error "${var} is not set"
            missing=1
        fi
    done
    [[ ${missing} -eq 0 ]]
}

urlencode() {
    local string="$1"
    local strlen=${#string}
    local encoded=""
    for (( i=0; i<strlen; i++ )); do
        local char="${string:i:1}"
        case "${char}" in
            [a-zA-Z0-9._~-]) encoded+="${char}" ;;
            *) encoded+=$(printf '%%%02X' "'${char}") ;;
        esac
    done
    echo "${encoded}"
}

send_matrix_message() {
    local room_id="$1"
    local msg_type="$2"
    local content="$3"
    
    local txn_id="worker_$(date +%s)_$$"
    
    curl -sf -X PUT \
        -H "Authorization: Bearer ${HICLAW_MATRIX_ACCESS_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{
            \"msgtype\": \"${msg_type}\",
            \"content\": ${content}
        }" \
        "${HICLAW_MATRIX_HOMESERVER}/_matrix/client/r0/rooms/$(urlencode "${room_id}")/send/m.room.message/${txn_id}" \
        || log_error "Failed to send matrix message"
}

register_worker() {
    local worker_name="${HICLAW_WORKER_NAME:-hermes-worker}"
    local device_id="${HICLAW_MATRIX_DEVICE_ID:-HERMES01}"
    
    log "Registering worker '${worker_name}' with manager room ${HICLAW_MANAGER_ROOM_ID}"
    
    local metadata=$(cat <<EOF
{
    "id": "$(hostname)-${worker_name}",
    "name": "${worker_name}",
    "capabilities": ["task-execution", "file-sync", "hermes-agent"],
    "status": "registered",
    "version": "1.0.0",
    "matrix_user_id": "${HICLAW_MATRIX_USER_ID}",
    "device_id": "${device_id}"
}
EOF
)
    
    send_matrix_message "${HICLAW_MANAGER_ROOM_ID}" "m.text" "${metadata}"
    log "Registration sent"
}

send_status() {
    local status="$1"
    local message="${2:-}"
    
    local worker_name="${HICLAW_WORKER_NAME:-hermes-worker}"
    
    local payload=$(cat <<EOF
{
    "status": "${status}",
    "worker": "${worker_name}",
    "timestamp": "$(date -Iseconds)"
    ${message:+, "message": "${message}"}
}
EOF
)
    
    send_matrix_message "${HICLAW_MANAGER_ROOM_ID}" "m.text" "${payload}"
    log "Status update: ${status}"
}

cmd_register() {
    if ! check_env; then
        log_error "Missing required environment variables"
        exit 1
    fi
    
    register_worker
    sleep 1
    send_status "ready"
}

cmd_status() {
    local status="${1:-ready}"
    local message="${2:-}"
    
    if ! check_env; then
        log_error "Missing required environment variables"
        exit 1
    fi
    
    send_status "${status}" "${message}"
}

cmd_lifecycle() {
    local cmd=("$@")
    
    if ! check_env; then
        log_error "Missing required environment variables"
        exit 1
    fi
    
    register_worker
    sleep 1
    send_status "ready"
    sleep 1
    send_status "busy"
    
    if [[ ${#cmd[@]} -gt 0 ]] && [[ -n "${cmd[0]}" ]]; then
        if "${cmd[@]}"; then
            send_status "done"
        else
            send_status "error" "Command failed: ${cmd[*]}"
            exit 1
        fi
    else
        send_status "done"
    fi
}

usage() {
    cat <<EOF
Usage: create-hermes-worker.sh <command> [options]

Commands:
    register                      Register worker and send ready status
    status <state> [message]     Send explicit status update
    lifecycle -- <cmd> [args]   Run command with automatic status transitions

Environment Variables:
    HICLAW_WORKER_NAME           Worker name (default: hermes-worker)
    HICLAW_MATRIX_HOMESERVER      Matrix server URL
    HICLAW_MATRIX_ACCESS_TOKEN    Matrix access token
    HICLAW_MATRIX_USER_ID         Matrix user ID
    HICLAW_MATRIX_DEVICE_ID       Device ID (default: HERMES01)
    HICLAW_MANAGER_ROOM_ID       Manager Matrix room ID

Examples:
    create-hermes-worker.sh register
    create-hermes-worker.sh status busy "Processing task"
    create-hermes-worker.sh lifecycle -- my-worker-command arg1 arg2
EOF
}

main() {
    if [[ $# -lt 1 ]]; then
        usage
        exit 1
    fi
    
    local command="$1"
    shift
    
    case "${command}" in
        register) cmd_register "$@"; ;;
        status)   cmd_status "$@"; ;;
        lifecycle)
            local dash_idx=0
            for i in "${!@}"; do
                if [[ "${!i}" == "--" ]]; then
                    dash_idx=$i
                    break
                fi
            done
            if [[ ${dash_idx} -gt 0 ]]; then
                local cmd=("${@:$((dash_idx+1))}")
                cmd_lifecycle "${cmd[@]}"
            else
                log_error "lifecycle requires -- separator"
                exit 1
            fi
            ;;
        help|--help|-h) usage; ;;
        *)
            log_error "Unknown command: ${command}"
            usage
            exit 1
            ;;
    esac
}

main "$@"
