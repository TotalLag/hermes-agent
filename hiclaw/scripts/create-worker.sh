#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [MANAGER] $*"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [MANAGER] ERROR: $*" >&2
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
    
    local txn_id="mgr_$(date +%s)_$$"
    
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

check_env() {
    local missing=0
    for var in HICLAW_MATRIX_HOMESERVER HICLAW_MATRIX_ACCESS_TOKEN HICLAW_MANAGER_ROOM_ID; do
        if [[ -z "${!var:-}" ]]; then
            log_error "${var} is not set"
            missing=1
        fi
    done
    [[ ${missing} -eq 0 ]]
}

cmd_register() {
    local worker_name="${1:-}"
    local worker_id="${2:-$(hostname)-${worker_name}}"
    local capabilities="${3:-task-execution,file-sync,hermes-agent}"
    
    if ! check_env; then
        log_error "Missing required environment variables"
        exit 1
    fi
    
    if [[ -z "${worker_name}" ]]; then
        log_error "Usage: create-worker.sh register <worker_name> [worker_id] [capabilities]"
        exit 1
    fi
    
    log "Registering worker '${worker_name}' (${worker_id})"
    
    local metadata=$(python3 -c "
import json, sys
caps = '${capabilities}'.split(',')
print(json.dumps({
    'id': '${worker_id}',
    'name': '${worker_name}',
    'capabilities': caps,
    'status': 'registered',
    'version': '1.0.0',
    'matrix_user_id': '${HICLAW_MATRIX_USER_ID:-}',
    'device_id': '${HICLAW_MATRIX_DEVICE_ID:-MANAGER01}'
}))
")
    
    send_matrix_message "${HICLAW_MANAGER_ROOM_ID}" "m.text" "${metadata}"
    log "Worker registration sent"
}

cmd_list() {
    if ! check_env; then
        log_error "Missing required environment variables"
        exit 1
    fi
    
    log "Workers are registered via Matrix. Use 'hiclaw_manager_list_workers' tool or"
    log "check the worker-registry.json at ~/.hermes/hiclaw/workers-registry.json"
    
    local registry_path="${HOME}/.hermes/hiclaw/workers-registry.json"
    if [[ -f "${registry_path}" ]]; then
        echo ""
        echo "Registered workers:"
        python3 "${SCRIPT_DIR}/list_workers_json.py" "${registry_path}" 2>/dev/null || cat "${registry_path}"
    else
        echo "No worker registry found at ${registry_path}"
    fi
}

cmd_status() {
    local worker_id="${1:-}"
    local status="${2:-}"
    local message="${3:-}"
    
    if ! check_env; then
        log_error "Missing required environment variables"
        exit 1
    fi
    
    if [[ -z "${worker_id}" ]] || [[ -z "${status}" ]]; then
        log_error "Usage: create-worker.sh status <worker_id> <status> [message]"
        exit 1
    fi
    
    log "Updating worker ${worker_id} status to ${status}"
    
    local payload=$(python3 -c "
import json
d = {
    'status': '${status}',
    'worker': '${worker_id}',
    'timestamp': __import__('datetime').datetime.utcnow().isoformat() + 'Z'
}
${message:+d['message'] = '${message}'}
print(json.dumps(d))
")
    
    send_matrix_message "${HICLAW_MANAGER_ROOM_ID}" "m.text" "${payload}"
    log "Status update sent"
}

usage() {
    cat <<EOF
Usage: create-worker.sh <command> [options]

Commands:
    register <worker_name> [worker_id] [capabilities]
                                            Register a new worker
    list                               List registered workers
    status <worker_id> <status> [message]
                                            Send status update for a worker

Environment Variables:
    HICLAW_MATRIX_HOMESERVER           Matrix server URL
    HICLAW_MATRIX_ACCESS_TOKEN         Matrix access token
    HICLAW_MATRIX_USER_ID              Matrix user ID (optional)
    HICLAW_MATRIX_DEVICE_ID            Device ID (default: MANAGER01)
    HICLAW_MANAGER_ROOM_ID            Manager Matrix room ID

Examples:
    create-worker.sh register my-worker worker-01 "terminal,web,file"
    create-worker.sh list
    create-worker.sh status worker-01 busy "Processing task"
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
        list)     cmd_list "$@"; ;;
        status)   cmd_status "$@"; ;;
        help|--help|-h) usage; ;;
        *)
            log_error "Unknown command: ${command}"
            usage
            exit 1
            ;;
    esac
}

main "$@"
