# Deploy on AWS Lightsail

Why: Zerodha only accepts order calls from a whitelisted static IP. The bot must run on the machine that owns that IP.

1. Lightsail: Ubuntu instance, static IP attached, Networking tab open ports 22, 80, 443.
2. Point a domain name at the static IP (an A record). A free dynamic-DNS name also works. Wait until it resolves.
3. In the instance SSH terminal:
   ```
   git clone https://github.com/<you>/<repo>.git kite && cd kite
   cp .env.example .env && nano .env     # fill in keys; DATA_DIR=./data; keep TRADING_MODE=paper
   bash deploy/lightsail/setup.sh your-domain.example.com
   ```
4. The script prints the outbound IP. It must match the IP whitelisted in the Kite console.
5. Kite console Redirect URL: `https://your-domain.example.com/kite/callback`.
6. Open `https://your-domain.example.com`, log in, watch prices in paper mode for a few days.
7. Stop the Railway service so two copies never run.

Updates: `bash deploy/lightsail/update.sh` (outside market hours).
Never commit `.env`. Back up `data/bot.db` if you want to keep trade history.
