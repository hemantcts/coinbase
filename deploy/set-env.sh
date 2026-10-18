#!/usr/bin/env bash
# Set one whitelisted key in the server's .env and restart the containers. Usage: sudo bash deploy/set-env.sh KEY VALUE
set -euo pipefail
KEY="${1:-}"; VALUE="${2:-}"
case "$KEY" in
  RISK_PROFILE|STRATEGY|HALT_RESET_DAYS|FEE_RATE|TAKER_FEE_RATE|PRODUCTS|NOTIFY_WEBHOOK|MAX_DATA_AGE|DASHBOARD_BIND_PORT|DASHBOARD_USER|DASHBOARD_PASSWORD|DASHBOARD_TOKEN|DASHBOARD_VIEWER_USER|DASHBOARD_VIEWER_PASSWORD) ;;
  *) echo "key '$KEY' is not allowed (never LIVE_TRADING or secrets from here)"; exit 1 ;;
esac
[ -n "$VALUE" ] || { echo "value required"; exit 1; }
cd "$(dirname "$0")/.."
[ -f .env ] || { echo ".env missing"; exit 1; }
if grep -q "^${KEY}=" .env; then
  sed -i "s|^${KEY}=.*|${KEY}=${VALUE}|" .env
else
  printf '%s=%s\n' "$KEY" "$VALUE" >> .env
fi
case "$KEY" in DASHBOARD_PASSWORD|DASHBOARD_VIEWER_PASSWORD|DASHBOARD_TOKEN|NOTIFY_WEBHOOK) echo "now: ${KEY}=***" ;; *) echo "now: $(grep "^${KEY}=" .env)" ;; esac
docker compose up -d --force-recreate trader dashboard   # restart alone keeps the old environment
sleep 8
docker compose logs --tail 6 --no-color trader
