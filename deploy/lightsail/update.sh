#!/usr/bin/env bash
# Pull new code and restart. Do NOT run during market hours (09:15-15:30 IST):
# a restart makes the bot forget which stops/targets it was managing.
set -euo pipefail
cd "$(dirname "$0")/../.."
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart kite-bot
sudo systemctl status kite-bot --no-pager | head -5
