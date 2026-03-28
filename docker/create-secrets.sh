#!/bin/bash
# =============================================================================
# HiClaw Docker Secrets Creation Script
# =============================================================================
# Creates Docker secrets from a directory containing secret files.
#
# Usage:
#   ./create-secrets.sh /path/to/secrets/directory/
#
# The directory should contain one file per secret:
#   hiclaw_matrix_access_token
#   hiclaw_matrix_user_id
#   hiclaw_matrix_homeserver_url
#   hiclaw_minio_access_key
#   hiclaw_minio_secret_key
#   hiclaw_worker_api_key
#
# Each file should contain only the secret value (no newlines at end).
#
# =============================================================================

set -e

SECRETS_DIR="${1:-.}"

# List of required secrets
SECRETS=(
    "hiclaw_matrix_access_token"
    "hiclaw_matrix_user_id"
    "hiclaw_matrix_homeserver_url"
    "hiclaw_minio_access_key"
    "hiclaw_minio_secret_key"
    "hiclaw_worker_api_key"
)

echo "=== HiClaw Docker Secrets Setup ==="
echo ""

# Check if Docker Swarm is initialized
if ! docker info 2>/dev/null | grep -q "Swarm: active"; then
    echo "ERROR: Docker Swarm is not initialized."
    echo "Run 'docker swarm init' first to initialize a single-node swarm."
    exit 1
fi

echo "Docker Swarm detected."
echo ""

# Check if secrets directory exists
if [ ! -d "$SECRETS_DIR" ]; then
    echo "ERROR: Secrets directory '$SECRETS_DIR' does not exist."
    exit 1
fi

echo "Creating secrets from: $SECRETS_DIR"
echo ""

# Function to create a secret
create_secret() {
    local secret_name="$1"
    local secret_file="${SECRETS_DIR}/${secret_name}"
    
    if [ -f "$secret_file" ]; then
        # Check if secret already exists
        if docker secret ls --format '{{.Name}}' | grep -q "^${secret_name}$"; then
            echo "  [SKIP] $secret_name (already exists)"
        else
            # Create secret from file (using cat to preserve exact content)
            local secret_value
            secret_value=$(cat "$secret_file")
            echo "$secret_value" | docker secret create "$secret_name" -
            echo "  [CREATED] $secret_name"
        fi
    else
        echo "  [WARN] $secret_name (file not found: $secret_file)"
    fi
}

# Create each secret
for secret in "${SECRETS[@]}"; do
    create_secret "$secret"
done

echo ""
echo "=== Secret Creation Complete ==="
echo ""
echo "To verify secrets, run: docker secret ls"
echo ""
echo "Deploy HiClaw with:"
echo "  docker-compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml -f docker/hiclaw-secrets.docker-compose.yml up"
