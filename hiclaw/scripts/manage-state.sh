#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [STATE] $*"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [STATE] ERROR: $*" >&2
}

usage() {
    cat <<EOF
Usage: manage-state.sh <command> [options]

Commands:
    mode                             Show current manager mode
    mode <idle|dispatching|monitoring>
                                        Set manager mode
    stats                            Show task/worker statistics
    list-workers [status]            List registered workers
    list-tasks [status]              List tasks
    get-worker <worker_id>           Get worker details
    get-task <task_id>               Get task details

Examples:
    manage-state.sh mode
    manage-state.sh mode autonomous
    manage-state.sh stats
    manage-state.sh list-workers ready
    manage-state.sh list-tasks pending
EOF
}

cmd_mode() {
    local mode="${1:-}"
    if [[ -z "${mode}" ]]; then
        python3 -c "
import asyncio
from gateway.hiclaw.manager_state import ManagerState, ManagerMode
state = ManagerState()
mode = asyncio.get_event_loop().run_until_complete(state.get_mode())
print(f'Current mode: {mode.value}')
"
    else
        case "${mode}" in
            idle|dispatching|monitoring) ;;
            *)
                log_error "Invalid mode: ${mode}. Use: idle, dispatching, monitoring"
                exit 1
                ;;
        esac
        python3 -c "
import asyncio
from gateway.hiclaw.manager_state import ManagerState, ManagerMode
state = ManagerState()
asyncio.get_event_loop().run_until_complete(state.set_mode(ManagerMode('${mode}')))
print('Mode set to: ${mode}')
"
    fi
}

cmd_stats() {
    python3 -c "
import asyncio
from gateway.hiclaw.manager_state import ManagerState
from gateway.hiclaw.worker_registry import WorkerRegistry
state = ManagerState()
registry = WorkerRegistry()
stats = asyncio.get_event_loop().run_until_complete(state.get_stats())
workers = asyncio.get_event_loop().run_until_complete(registry.list_workers())
print('Manager Statistics:')
print(f'  Total tasks: {stats.get(\"total_tasks\", 0)}')
print(f'  Completed: {stats.get(\"completed_tasks\", 0)}')
print(f'  Failed: {stats.get(\"failed_tasks\", 0)}')
print(f'  Active workers: {len(workers)}')
"
}

cmd_list_workers() {
    local status_filter="${1:-}"
    local script="
import asyncio
import json
from gateway.hiclaw.worker_registry import WorkerRegistry
registry = WorkerRegistry()
workers = asyncio.get_event_loop().run_until_complete(
    registry.list_workers(${status_filter:+\"${status_filter}\"})
)
if not workers:
    print('No workers registered')
else:
    for w in workers:
        print(f\"  {w.name} ({w.id}): {w.status} | capabilities: {','.join(w.capabilities)} | last_seen: {w.last_seen_at}\")
"
    python3 -c "${script}"
}

cmd_list_tasks() {
    local status_filter="${1:-}"
    local script="
import asyncio
from gateway.hiclaw.manager_state import ManagerState
state = ManagerState()
tasks = asyncio.get_event_loop().run_until_complete(
    state.list_tasks(${status_filter:+\"${status_filter}\"})
)
if not tasks:
    print('No tasks found')
else:
    for t in tasks:
        print(f\"  {t.id}: {t.status} | worker: {t.assigned_worker or '(unassigned)'} | created: {t.created_at}\")
"
    python3 -c "${script}"
}

cmd_get_worker() {
    local worker_id="${1:-}"
    if [[ -z "${worker_id}" ]]; then
        log_error "Usage: manage-state.sh get-worker <worker_id>"
        exit 1
    fi
    python3 -c "
import asyncio
import json
from gateway.hiclaw.worker_registry import WorkerRegistry
from gateway.hiclaw.lifecycle_logger import LifecycleLogger
registry = WorkerRegistry()
worker = asyncio.get_event_loop().run_until_complete(registry.get_worker('${worker_id}'))
if not worker:
    print('Worker not found: ${worker_id}')
else:
    logger = LifecycleLogger()
    history = logger.get_worker_history('${worker_id}')
    print(f\"Worker: {worker.name} ({worker.id})\")
    print(f\"  Status: {worker.status}\")
    print(f\"  Capabilities: {','.join(worker.capabilities)}\")
    print(f\"  Version: {worker.version}\")
    print(f\"  Matrix: {worker.matrix_user_id}\")
    print(f\"  Registered: {worker.registered_at}\")
    print(f\"  Last seen: {worker.last_seen_at}\")
    if history:
        print(f\"  Recent events: {len(history)}\")
        for e in history[-5:]:
            print(f\"    - {e['event_type']}: {e['timestamp']}\")
" 2>&1 || echo "Worker not found: ${worker_id}"
}

cmd_get_task() {
    local task_id="${1:-}"
    if [[ -z "${task_id}" ]]; then
        log_error "Usage: manage-state.sh get-task <task_id>"
        exit 1
    fi
    python3 -c "
import asyncio
from gateway.hiclaw.manager_state import ManagerState
state = ManagerState()
task = asyncio.get_event_loop().run_until_complete(state.get_task('${task_id}'))
if not task:
    print('Task not found: ${task_id}')
else:
    print(f\"Task: {task.id}\")
    print(f\"  Status: {task.status}\")
    print(f\"  Spec: {task.spec_path}\")
    print(f\"  Worker: {task.assigned_worker or '(unassigned)'}\")
    print(f\"  Created: {task.created_at}\")
    print(f\"  Updated: {task.updated_at}\")
    if task.result_path:
        print(f\"  Result: {task.result_path}\")
    if task.error:
        print(f\"  Error: {task.error}\")
" 2>&1 || echo "Task not found: ${task_id}"
}

main() {
    if [[ $# -lt 1 ]]; then
        usage
        exit 1
    fi
    
    local command="$1"
    shift
    
    case "${command}" in
        mode)         cmd_mode "$@"; ;;
        stats)        cmd_stats "$@"; ;;
        list-workers) cmd_list_workers "$@"; ;;
        list-tasks)   cmd_list_tasks "$@"; ;;
        get-worker)   cmd_get_worker "$@"; ;;
        get-task)     cmd_get_task "$@"; ;;
        help|--help|-h) usage; ;;
        *)
            log_error "Unknown command: ${command}"
            usage
            exit 1
            ;;
    esac
}

main "$@"
