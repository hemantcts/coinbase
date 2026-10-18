#!/usr/bin/env bash
# Regenerate the dashboard password and API token on the server. Prints nothing secret.
set -euo pipefail
cd "$(dirname "$0")/.."
NEWPW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))' 2>/dev/null || openssl rand -hex 18)"
NEWTOK="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))' 2>/dev/null || openssl rand -hex 24)"
for kv in "DASHBOARD_PASSWORD=$NEWPW" "DASHBOARD_TOKEN=$NEWTOK"; do
  k="${kv%%=*}"
  if grep -q "^${k}=" .env; then sed -i "s|^${k}=.*|${kv}|" .env; else echo "$kv" >> .env; fi
done
grep -q '^DASHBOARD_USER=.\+' .env || echo 'DASHBOARD_USER=admin' >> .env
docker compose up -d --force-recreate dashboard trader >/dev/null 2>&1
echo "rotated DASHBOARD_PASSWORD and DASHBOARD_TOKEN; old links and sessions are invalid."
echo "read the new login on the server with: sudo grep DASHBOARD_ $(pwd)/.env"
