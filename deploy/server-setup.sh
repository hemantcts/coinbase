#!/usr/bin/env bash
# Idempotent server-side setup, run by the GitHub Actions deploy (or by hand with sudo) after the
# code has been copied to APP_DIR. Safe on a CloudPanel host: never touches an active firewall or nginx.
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/coinbase-trader}"
cd "$APP_DIR"

echo "==> packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl rsync unattended-upgrades >/dev/null

echo "==> firewall"
if ufw status 2>/dev/null | grep -q "Status: active"; then
  echo "    ufw already active (CloudPanel manages it): left untouched"
else
  apt-get install -y -qq ufw >/dev/null
  ufw default deny incoming >/dev/null; ufw default allow outgoing >/dev/null
  ufw allow OpenSSH >/dev/null; ufw --force enable >/dev/null
  echo "    ufw enabled, SSH only"
fi

echo "==> docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh >/dev/null
fi
systemctl enable --now docker >/dev/null
docker compose version >/dev/null

echo "==> configuration"
if [ ! -f .env ]; then
  cp .env.example .env
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))' 2>/dev/null || openssl rand -hex 24)"
  sed -i "s|^DASHBOARD_TOKEN=.*|DASHBOARD_TOKEN=${TOKEN}|" .env
  echo "    created .env with a fresh DASHBOARD_TOKEN"
else
  echo "    .env kept"
fi
if ! grep -q '^DASHBOARD_PASSWORD=.\+' .env; then
  PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))' 2>/dev/null || openssl rand -hex 18)"
  grep -q '^DASHBOARD_USER=' .env && sed -i 's|^DASHBOARD_USER=.*|DASHBOARD_USER=admin|' .env || echo 'DASHBOARD_USER=admin' >> .env
  grep -q '^DASHBOARD_PASSWORD=' .env && sed -i "s|^DASHBOARD_PASSWORD=.*|DASHBOARD_PASSWORD=${PW}|" .env || echo "DASHBOARD_PASSWORD=${PW}" >> .env
  echo "    created dashboard login (user admin); read it with: sudo grep DASHBOARD_ $APP_DIR/.env"
fi
if ! grep -q '^DASHBOARD_VIEWER_PASSWORD=.\+' .env; then
  VPW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(14))' 2>/dev/null || openssl rand -hex 14)"
  grep -q '^DASHBOARD_VIEWER_USER=' .env && sed -i 's|^DASHBOARD_VIEWER_USER=.*|DASHBOARD_VIEWER_USER=client|' .env || echo 'DASHBOARD_VIEWER_USER=client' >> .env
  grep -q '^DASHBOARD_VIEWER_PASSWORD=' .env && sed -i "s|^DASHBOARD_VIEWER_PASSWORD=.*|DASHBOARD_VIEWER_PASSWORD=${VPW}|" .env || echo "DASHBOARD_VIEWER_PASSWORD=${VPW}" >> .env
  echo "    created view-only login (user client)"
fi
grep -q '^LIVE_TRADING=true' .env && { echo "REFUSING: LIVE_TRADING=true in .env on an automated deploy"; exit 1; }
mkdir -p data data/history
chown -R 10001:10001 data
chmod 700 data

echo "==> daily database backup (14 days kept)"
cat > /etc/cron.daily/coinbase-trader-backup <<CRON
#!/bin/sh
# Copy the trader database with sqlite's own backup so a running writer is safe; keep 14 days.
mkdir -p "$APP_DIR/data/backups"
docker compose -f "$APP_DIR/compose.yaml" --project-directory "$APP_DIR" exec -T trader \
  python -c "import sqlite3,datetime; s=sqlite3.connect('/data/trader.sqlite3'); d=sqlite3.connect('/data/backups/trader-%s.sqlite3' % datetime.date.today().isoformat()); s.backup(d); d.close()" 2>/dev/null
find "$APP_DIR/data/backups" -name 'trader-*.sqlite3' -mtime +14 -delete
CRON
chmod 755 /etc/cron.daily/coinbase-trader-backup

echo "==> file ownership"
# Inside a CloudPanel site directory, give the code to the site user so the File Manager can show and
# edit it; the data directory stays with the container user (10001).
PARENT_OWNER="$(stat -c %U "$(dirname "$APP_DIR")" 2>/dev/null || echo root)"
if [ "$PARENT_OWNER" != "root" ] && id "$PARENT_OWNER" >/dev/null 2>&1; then
  find "$APP_DIR" -path "$APP_DIR/data" -prune -o -exec chown "$PARENT_OWNER:$PARENT_OWNER" {} + 2>/dev/null || true
  echo "    code owned by $PARENT_OWNER, data by 10001"
fi

echo "==> start / restart containers"
docker compose up -d --build --remove-orphans 2>&1 | grep -v "^$" | tail -5
sleep 8
docker compose ps
echo "==> import committed candle history for backtests and the gate"
docker compose exec -T trader python -m trader import-history || echo "    (import skipped)"
echo "==> last trader log lines"
docker compose logs --tail 8 trader
