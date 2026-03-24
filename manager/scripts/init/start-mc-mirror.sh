#!/bin/bash
# start-mc-mirror.sh - Initialize MinIO storage and start periodic Remote->Local sync

source /opt/hiclaw/scripts/lib/hiclaw-env.sh
waitForService "MinIO" "127.0.0.1" 9000

mc alias set hiclaw http://127.0.0.1:9000 "${HICLAW_MINIO_USER:-${HICLAW_ADMIN_USER:-admin}}" "${HICLAW_MINIO_PASSWORD:-${HICLAW_ADMIN_PASSWORD:-admin}}"
mc mb "${HICLAW_STORAGE_PREFIX}" --ignore-existing

for dir in shared/knowledge shared/tasks workers; do
    echo "" | mc pipe "${HICLAW_STORAGE_PREFIX}/${dir}/.gitkeep" 2>/dev/null || true
done

HICLAW_FS_ROOT="/root/hiclaw-fs"
mkdir -p "${HICLAW_FS_ROOT}"
mc mirror "${HICLAW_STORAGE_PREFIX}/" "${HICLAW_FS_ROOT}/" --overwrite
touch "${HICLAW_FS_ROOT}/.initialized"

log "MinIO storage initialized and synced to ${HICLAW_FS_ROOT}/"

while true; do
    sleep 300
    mc mirror "${HICLAW_STORAGE_PREFIX}/" "${HICLAW_FS_ROOT}/" --overwrite --newer-than "5m" 2>/dev/null || true
done
