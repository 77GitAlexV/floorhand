#!/bin/sh
set -eu

# Maintenance commands must not seed a missing database as a side effect.
if [ "$1" = python ] && [ "${2:-}" = -m ] && [ "${3:-}" = uvicorn ]; then
    python -m deploy.runtime init
fi
exec "$@"
