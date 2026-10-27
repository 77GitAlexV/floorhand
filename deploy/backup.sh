#!/bin/sh
set -eu
cd "$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"

# Use the same host lock for backup and reset, including cron invocations.
exec 9>.deploy-maintenance.lock
flock -n 9 || { echo 'Maintenance already running. / Обслуживание уже идёт.' >&2; exit 1; }
docker compose run --rm --no-deps -T app python -m deploy.runtime backup --keep "${BACKUP_KEEP:-7}"
