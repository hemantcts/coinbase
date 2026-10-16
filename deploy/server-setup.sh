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
grep -q '^LIVE_TRADING=true' .env && { echo "REFUSING: LIVE_TRADING=true in .env on an automated deploy"; exit 1; }
mkdir -p data
chown -R 10001:10001 data
chmod 700 data

echo "==> start / restart containers"
docker compose up -d --build --remove-orphans 2>&1 | grep -v "^$" | tail -5
sleep 8
docker compose ps
echo "==> last trader log lines"
docker compose logs --tail 8 trader
