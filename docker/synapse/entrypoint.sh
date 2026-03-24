#!/bin/bash
set -e

DATA_DIR=/data
SERVER_NAME=${SYNAPSE_SERVER_NAME:-hermes.local}
REGISTRATION_SHARED_SECRET=${SYNAPSE_REGISTRATION_SHARED_SECRET:-}
PUBLIC_BASE_URL=${SYNAPSE_PUBLIC_BASEURL:-http://synapse:8008}

if [ ! -f "$DATA_DIR/homeserver.yaml" ]; then
    python -m synapse.app.homeserver \
        --generate-config \
        --server-name "$SERVER_NAME" \
        --data-dir "$DATA_DIR" \
        --report-stats=no
fi

if [ -n "$REGISTRATION_SHARED_SECRET" ]; then
    sed -i "s/registration_shared_secret:.*/registration_shared_secret: $REGISTRATION_SHARED_SECRET/" \
        "$DATA_DIR/homeserver.yaml"
fi

sed -i "s|public_baseurl:.*|public_baseurl: $PUBLIC_BASE_URL|" \
    "$DATA_DIR/homeserver.yaml"

sed -i 's/enable_registration:.*/enable_registration: true/' \
    "$DATA_DIR/homeserver.yaml"

exec python -m synapse.app.homeserver -c "$DATA_DIR/homeserver.yaml"
