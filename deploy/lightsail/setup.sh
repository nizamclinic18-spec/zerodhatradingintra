#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu Lightsail instance.
# Usage (from the project folder):  bash deploy/lightsail/setup.sh your-domain.example.com
set -euo pipefail

DOMAIN="${1:-}"
if [ -z "$DOMAIN" ]; then
  echo "Usage: bash deploy/lightsail/setup.sh <your-domain>"; exit 1
fi
APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
APP_USER="$(whoami)"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "Create $APP_DIR/.env first (copy .env.example and fill it in)."; exit 1
fi

echo "== Installing system packages"
sudo apt update
sudo apt install -y python3-venv python3-pip git curl debian-keyring debian-archive-keyring apt-transport-https gpg

echo "== Python environment"
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
mkdir -p data

echo "== Installing Caddy (automatic HTTPS)"
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt update
  sudo apt install -y caddy
fi
sed "s/YOUR_DOMAIN/$DOMAIN/" deploy/lightsail/Caddyfile | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl restart caddy

echo "== Installing the bot as a service"
sed -e "s#APP_DIR#$APP_DIR#g" -e "s#APP_USER#$APP_USER#g" deploy/lightsail/kite-bot.service | sudo tee /etc/systemd/system/kite-bot.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now kite-bot

echo
echo "Done. Outbound IP seen by Zerodha (must equal your whitelisted static IP):"
curl -s ifconfig.me; echo
echo "Dashboard: https://$DOMAIN   Kite redirect URL: https://$DOMAIN/kite/callback"
echo "Logs: sudo journalctl -u kite-bot -f"
