# Intraday desk

Zerodha Kite Connect intraday bot (equity, MIS) with a web dashboard. One FastAPI service on Railway serves both the bot and the dashboard.

Starts in **paper mode**. Nothing places a real order until you set `TRADING_MODE=live` *and* type LIVE in the dashboard each morning.

## What it does
- Streams prices (KiteTicker), builds 5-minute candles, runs a sample opening-range-breakout strategy (`app/strategy.py`, replace with your own).
- Places entry, waits for the fill, then places an exchange-side SL-limit sized to the filled quantity.
- Watchdog every 3s: day P&L vs loss limit, stop-loss fill/loss detection, 15:10 IST square-off.
- Kill-switch (automatic at the loss limit, or manual): writes a daily lock first, cancels MIS orders, exits positions one by one with retries. Lock is stored in SQLite on a Railway Volume, so restarts and redeploys do not unlock it.
- Dashboard: day P&L against the loss limit, positions, closed trades, opening ranges, activity log, Arm/Disarm/Kill controls.

## Stock tracker (alerts only)
The dashboard's Stock tracker card follows any NSE stocks you add (up to 30) with the same strategy as the bot and lists each signal with entry, stop, target and a suggested quantity, so you can place those trades yourself in Zerodha. It never places orders and does not affect the bot's own watchlist. Alerts follow the bot's entry window; breakouts that happened before you added a stock appear tagged "earlier today". Tick the sound box to hear new alerts while the dashboard is open.

## Restarts
State is saved every few seconds (armed flag, attempted symbols, open trades with their stops and targets, paper positions). After a restart the bot restores it, rebuilds today's opening ranges from Zerodha's price history, and never trades breakouts that happened while it was down. The Activity log lists every skip reason (wide stop, incomplete range, disarmed, outside entry window). Restarts are still best avoided during market hours.

## Deploy
1. Push to GitHub:
   ```
   git init && git add . && git commit -m "intraday desk"
   git branch -M main && git remote add origin https://github.com/<you>/kite-intraday.git && git push -u origin main
   ```
2. Railway: New Project > Deploy from GitHub repo. Add a **Volume** mounted at `/data`.
3. Railway Variables: copy everything from `.env.example`. Set `DATA_DIR=/data`, a long random `DASHBOARD_PASSWORD`, your `KITE_API_KEY` and `KITE_API_SECRET`. Keep `TRADING_MODE=paper`.
4. Generate a domain in Railway. In the Kite developer console set the **Redirect URL** to `https://<your-domain>/kite/callback`.
5. Static IP: from 1 April 2026 Zerodha accepts order placement (place/modify/cancel) only from a whitelisted static IP. Paper mode places no orders, so you can test without it. Before going live, get a fixed outbound IP (check Railway's static outbound IP option for your plan, or use a small VPS) and add it in the Kite developer console.

## Deploy on AWS Lightsail instead
See `deploy/lightsail/README.md`. `railway.json` is ignored there. Use this route if you need your own static IP for live orders.

## Every trading morning
Open the dashboard, Log in to Zerodha (login expires daily, do not automate it), check the watchlist shows prices, then Arm before 09:30.

## Before real money
- Run paper mode for several weeks and look at the closed-trades history. The sample strategy has no proven edge.
- Go live with a tiny `CAPITAL` first. Check brokerage and taxes yourself: displayed P&L is gross.
- The bot manages the whole MIS book of the account. Positions you open manually in the same account count toward the loss limit and are squared off by the kill-switch and EOD exit.
- Keep one worker only (the Railway config does this).

## Tests
`pip install -r requirements.txt -r requirements-dev.txt && pytest`

This is software, not financial advice. Algo trading can lose money quickly; SEBI/exchange rules for retail algos apply to you.
