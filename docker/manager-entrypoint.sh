#!/bin/bash
# Enforce resource limits if set
if [ -n "$HERMES_MEMORY_LIMIT" ]; then
    # Resource limits are enforced by docker-compose or container runtime
    # This script allows pre-flight checks before the agent starts
    echo "[hiclaw-manager] Starting with memory limit: $HERMES_MEMORY_LIMIT"
fi
if [ -n "$HERMES_CPU_LIMIT" ]; then
    echo "[hiclaw-manager] Starting with CPU limit: $HERMES_CPU_LIMIT"
fi

exec hermes agent \
     --model "${HICLAW_LLM_PROVIDER:-openai-compat}:${HICLAW_DEFAULT_MODEL:-gpt-4o}" \
     --platform matrix \
     --skills hiclaw/manager
