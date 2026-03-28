#!/bin/bash
# Graceful shutdown handler
cleanup() {
    echo "[hiclaw-worker] Received SIGTERM, saving state..."
    # Signal to hermes that we want to finish current task
    # The agent will complete and exit cleanly
    kill -TERM $$ 2>/dev/null
}
trap cleanup TERM INT

echo "[hiclaw-worker] Starting worker: ${HICLAW_WORKER_NAME:-unknown} (v${HICLAW_WORKER_VERSION:-dev})"
exec hermes agent \
     --model "${HICLAW_LLM_PROVIDER:-openai-compat}:${HICLAW_DEFAULT_MODEL:-gpt-4o}" \
     --platform matrix \
     --skills hiclaw/worker-lifecycle
