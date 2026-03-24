# =============================================================================
# hermes-agent hiclaw Manager — Drop-in replacement for OpenClaw Manager
# =============================================================================
# Build: docker build -t ghcr.io/totallag/hermes-manager:latest .
# Use:   HICLAW_INSTALL_MANAGER_IMAGE=ghcr.io/totallag/hermes-manager:latest bash <(curl -sSL ...)
#
# This image replaces the OpenClaw Manager in a hiclaw deployment.
# It expects the hiclaw platform (Higress, Tuwunel, MinIO, Element Web)
# to already be running — either co-located in the same compose stack or
# reachable via network.
#
# Required env vars (passed by hiclaw install script / compose):
#   HICLAW_MATRIX_DOMAIN       — Matrix homeserver (e.g. matrix-local.hiclaw.io:18080)
#   HICLAW_AI_GATEWAY_DOMAIN   — AI gateway domain (e.g. aigw-local.hiclaw.io)
#   HICLAW_FS_DOMAIN           — Filesystem domain (e.g. fs-local.hiclaw.io)
#   HICLAW_CONTAINER_API       — Docker proxy URL (e.g. http://hiclaw-docker-proxy:2375)
#   HICLAW_LLM_API_KEY         — LLM API key
#   HICLAW_LLM_PROVIDER        — LLM provider (e.g. openai-compat)
#   HICLAW_OPENAI_BASE_URL     — OpenAI-compatible base URL
#   HICLAW_DEFAULT_MODEL       — Default model name
#   HICLAW_MANAGER_GATEWAY_KEY — Manager gateway API key (auto-generated if not set)
#   HICLAW_MANAGER_PASSWORD    — Matrix bot password (auto-generated if not set)
#   HICLAW_ADMIN_USER          — Admin Matrix user
#   HICLAW_ADMIN_PASSWORD     — Admin Matrix password
#   HICLAW_MINIO_USER         — MinIO root user
#   HICLAW_MINIO_PASSWORD      — MinIO root password
#   HICLAW_REGISTRATION_TOKEN  — Worker registration token
#   HICLAW_DATA_DIR            — Data directory (e.g. hiclaw-data)
#   HICLAW_WORKSPACE_DIR       — Workspace directory (e.g. /root/manager-workspace)
#   HICLAW_DOCKER_PROXY        — Enable docker proxy (e.g. 1)
#   HICLAW_MATRIX_E2EE         — Enable Matrix E2EE (e.g. 0)
#   HICLAW_DEFAULT_WORKER_RUNTIME — Default worker runtime (e.g. copaw)
#   HICLAW_WORKER_IDLE_TIMEOUT — Worker idle timeout in seconds (e.g. 720)
#   HICLAW_SKILLS_API_URL      — Skills API URL (optional)
#
# MinIO-compatible env vars (for task spec sync):
#   HICLAW_MC_HOST             — MinIO alias (e.g. myminio)
#   HICLAW_BUCKET              — MinIO bucket (e.g. hiclaw)
#   HICLAW_ACCESS_KEY          — MinIO access key
#   HICLAW_SECRET_KEY          — MinIO secret key
#   HICLAW_TASK_SPECS_PREFIX   — Prefix for task specs in MinIO (e.g. task-specs/)
#   HICLAW_TASK_RESULTS_PREFIX — Prefix for task results in MinIO (e.g. task-results/)
# =============================================================================

FROM python:3.11-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Build hermes-agent (clone and install in editable mode)
WORKDIR /build
RUN git clone --depth 1 https://github.com/TotalLag/hermes-agent.git .
RUN uv venv /opt/hermes-env --python 3.11
RUN . /opt/hermes-env/bin/activate && \
    uv pip install -e ".[dev]" && \
    uv pip install httpx aiohttp Pillow python-dotenv

# =============================================================================
# Final stage — layer on openclaw-base to reuse infrastructure services
# =============================================================================
FROM ubuntu:22.04

# Install Node.js 22 and other runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    supervisord \
    mc \
    && rm -rf /var/lib/apt/lists/*

# Copy hermes-agent from builder
COPY --from=builder /opt/hermes-env /opt/hermes-env
COPY --from=builder /build /opt/hermes-source

# Install hermes CLI globally
RUN . /opt/hermes-env/bin/activate && \
    ln -s /opt/hermes-env/bin/hermes /usr/local/bin/hermes && \
    ln -s /opt/hermes-env/bin/hermes-gateway /usr/local/bin/hermes-gateway

# Copy hiclaw scripts (needed for init sequence)
# These come from the hiclaw install and are mounted at /opt/hiclaw
# This layer ensures the image works even without the mount
RUN mkdir -p /opt/hiclaw/scripts/init /opt/hiclaw/scripts/lib /opt/hiclaw/scripts/upgrade

# ── hiclaw infrastructure ───────────────────────────────────────────────────
# Copy infrastructure binaries from builder or install directly

# MinIO
COPY --from=minio/minio:latest /usr/local/bin/minio /usr/local/bin/minio

# mc (MinIO client) — already present but ensure it's there
COPY --from=minio/mc:latest /usr/bin/mc /usr/bin/mc

# Tuwunel (conduwuit fork) — copy from hiclaw-manager image
COPY --from=higress-registry.us-west-1.cr.aliyuncs.com/higress/hiclaw-manager:latest /usr/local/bin/tuwunel /usr/local/bin/tuwunel

# Element Web
COPY --from=higress-registry.us-west-1.cr.aliyuncs.com/higress/hiclaw-manager:latest /opt/element-web /opt/element-web

# Higress binaries and scripts
COPY --from=higress-registry.us-west-1.cr.aliyuncs.com/higress/hiclaw-manager:latest /usr/local/bin/start-*.sh /usr/local/bin/
COPY --from=higress-registry.us-west-1.cr.aliyuncs.com/higress/hiclaw-manager:latest /usr/local/bin/base.sh /usr/local/bin/
COPY --from=higress-registry.us-west-1.cr.aliyuncs.com/higress/hiclaw-manager:latest /usr/local/bin/higress-proxy-start.sh /usr/local/bin/
COPY --from=higress-registry.us-west-1.cr.aliyuncs.com/higress/hiclaw-manager:latest /usr/local/bin/pilot-discovery /usr/local/bin/
COPY --from=higress-registry.us-west-1.cr.aliyuncs.com/higress/hiclaw-manager:latest /usr/local/bin/higress-proxy /usr/local/bin/
COPY --from=higress-registry.us-west-1.cr.aliyuncs.com/higress/hiclaw-manager:latest /etc/istio /etc/istio
COPY --from=higress-registry.us-west-1.cr.aliyuncs.com/higress/hiclaw-manager:latest /opt/java /opt/java

# Higress config
COPY --from=higress-registry.us-west-1.cr.aliyuncs.com/higress/hiclaw-manager:latest /app /app

# nginx (should already be in ubuntu)
RUN apt-get update && apt-get install -y --no-install-recommends nginx openjdk-21-jre-headless netcat-traditional curl && rm -rf /var/lib/apt/lists/*

# Create required directories
RUN mkdir -p /data/minio /data/tuwunel /root/hiclaw-fs /root/manager-workspace /var/log/hiclaw /etc/nginx/conf.d /etc/nginx/sites-enabled

# Copy hiclaw scripts (from the scripts/ directory in this repo)
COPY manager/scripts/ /opt/hiclaw/scripts/
RUN chmod +x /opt/hiclaw/scripts/init/*.sh /opt/hiclaw/scripts/lib/*.sh /usr/local/bin/start-*.sh 2>/dev/null || true

# Environment
ENV PATH="/opt/hermes-env/bin:$PATH"
ENV HERMES_HOME="/root/.hermes"
ENV HOME="/root"

# Working directory
WORKDIR /root

# Supervisord config — runs all hiclaw infrastructure services
# The hermes-agent Manager replaces the manager-agent service
COPY manager/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# hermes-entrypoint handles init (Matrix registration, Higress setup, secrets)
# then launches hermes-agent
COPY manager/hermes-entrypoint.sh /usr/local/bin/hermes-entrypoint.sh
RUN chmod +x /usr/local/bin/hermes-entrypoint.sh

COPY manager/run-gateway.sh /usr/local/bin/run-gateway.sh
RUN chmod +x /usr/local/bin/run-gateway.sh

# Default command — start supervisord (all infrastructure services + hermes-agent)
ENTRYPOINT ["/usr/local/bin/hermes-entrypoint.sh"]
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
