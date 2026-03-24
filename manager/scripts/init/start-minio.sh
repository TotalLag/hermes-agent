#!/bin/bash
# start-minio.sh - Start MinIO object storage (single node, single disk)

export MINIO_ROOT_USER="${HICLAW_MINIO_USER:-${HICLAW_ADMIN_USER:-admin}}"
export MINIO_ROOT_PASSWORD="${HICLAW_MINIO_PASSWORD:-${HICLAW_ADMIN_PASSWORD:-admin}}"

mkdir -p /data/minio

exec minio server /data/minio --console-address ":9001" --address ":9000"
