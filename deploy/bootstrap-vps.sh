#!/usr/bin/env bash
# One-shot setup of the paper-trading agent on a fresh Ubuntu 22.04/24.04 VPS (EU region).
# Run as root:  bash <(curl -fsSL https://raw.githubusercontent.com/hemantcts/coinbase/main/deploy/bootstrap-vps.sh)
# Or copy the file over and run:  sudo bash bootstrap-vps.sh
set -euo pipefail

REPO="${REPO:-https://github.com/hemantcts/coinbase.git}"
APP_DIR="${APP_DIR:-/opt/coinbase-trader}"
BRANCH="${BRANCH:-main}"

echo "==> 1/6 System packages and automatic security updates"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl git ufw unattended-upgrades fail2ban >/dev/null
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> 2/6 Firewall: SSH only, nothing else inbound"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
ufw --force enable >/dev/null
systemctl enable --now fail2ban >/dev/null

echo "==> 3/6 Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh >/dev/null
fi
systemctl enable --now docker >/dev/null

echo "==> 4/6 Code"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch -q origin "$BRANCH" && git -C "$APP_DIR" checkout -q "$BRANCH" && git -C "$APP_DIR" pull -q
else
  git clone -q --branch "$BRANCH" "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"

echo "==> 5/6 Configuration"
if [ ! -f .env ]; then
  cp .env.example .env
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))' 2>/dev/null || openssl rand -base64 24 | tr -d '/+=')"
  sed -i "s|^DASHBOARD_TOKEN=.*|DASHBOARD_TOKEN=${TOKEN}|" .env
  echo "    wrote .env with a fresh DASHBOARD_TOKEN (see: grep DASHBOARD_TOKEN $APP_DIR/.env)"
else
  echo "    .env exists, left untouched"
fi
mkdir -p data
chown -R 10001:10001 data
chmod 700 data

echo "==> 6/6 Start paper trader + dashboard"
docker compose up -d --build --quiet-pull 2>&1 | tail -3
sleep 5
docker compose ps

cat <<MSG

Paper trading is running. Useful commands (from $APP_DIR):
  docker compose logs -f trader              # live log
  docker compose exec trader python -m trader status
  docker compose exec trader python -m trader control stop|resume
  docker compose exec trader python -m trader gate --days 730 --granularity 21600
  docker compose pull && docker compose up -d --build    # after a git pull

Dashboard: from your own computer run
  ssh -L 8080:127.0.0.1:8080 root@<this-server-ip>
then open http://127.0.0.1:8080 and paste the DASHBOARD_TOKEN from .env.

LIVE_TRADING stays false. The live broker also needs data/LIVE_ARMED, a passed gate and a trade-only API key.
MSG
