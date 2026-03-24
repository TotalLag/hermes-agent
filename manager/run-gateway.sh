#!/bin/bash
# Thin wrapper: supervisord runs this, it execs into hermes-entrypoint.sh
# This lets supervisord track the gateway process without shell wrapper complications.
exec /usr/local/bin/hermes-entrypoint.sh
