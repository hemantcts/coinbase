#!/usr/bin/env bash
# One-time move of the running app (code, .env, data) to a new directory. Usage: sudo bash deploy/migrate.sh OLD_DIR NEW_DIR
set -euo pipefail
OLD="${1:?old dir}"; NEW="${2:?new dir}"
[ -d "$OLD" ] || { echo "old dir $OLD missing"; exit 1; }
[ "$OLD" != "$NEW" ] || { echo "same dir"; exit 1; }
echo "==> stop containers in $OLD"
( cd "$OLD" && docker compose down --remove-orphans ) || true
echo "==> copy to $NEW (code, .env, data, backups)"
mkdir -p "$NEW"
rsync -a --exclude '.git' "$OLD/" "$NEW/"
chown -R 10001:10001 "$NEW/data"
chmod 700 "$NEW/data"
echo "==> start containers in $NEW"
cd "$NEW"
docker compose up -d --build
sleep 8
docker compose ps
docker compose logs --tail 5 --no-color trader
echo "==> done. The old directory was left in place; delete the old CloudPanel site only after this output shows the trader running."
