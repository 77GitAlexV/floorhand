#!/bin/sh
set -eu
cd "$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"

exec 9>.deploy-maintenance.lock
flock -n 9 || { echo 'Maintenance already running. / Обслуживание уже идёт.' >&2; exit 1; }
# Check the demo guard before interrupting visitors.
docker compose run --rm --no-deps -T app python -c 'import os, sys; sys.exit(0 if os.environ.get("FLOORHAND_DEMO") == "1" else "Set FLOORHAND_DEMO=1. / Задайте FLOORHAND_DEMO=1.")'
# Restore service even when backup or seeding fails; the old DB stays intact.
trap 'docker compose up -d app' EXIT
trap 'exit 1' HUP INT TERM
docker compose stop app
docker compose run --rm --no-deps -T app python -m deploy.runtime reset
docker compose up -d --wait app
trap - EXIT HUP INT TERM
echo 'Demo ready; sign in again. / Демобаза готова; войдите заново.'
