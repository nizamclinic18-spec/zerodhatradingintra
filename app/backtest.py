"""Backtester. Uses the SAME strategy classes, risk sizing, cutoffs, loss limit and charges as the live engine.
Conservative choices: if stop and target are both inside one candle the stop is assumed hit first;
entries pay slippage; gaps through a stop fill at the open."""
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta

from .clock import IST
from .costs import trade_cost
from .risk import Risk


def _path(cfg, sym):
    return os.path.join(cfg.data_dir, "hist", f"{sym}.json")


def download(cfg, kite, tokens, days, status):
    end = datetime.now(IST).replace(tzinfo=None)
    start = end - timedelta(days=days)
    os.makedirs(os.path.join(cfg.data_dir, "hist"), exist_ok=True)
    for i, (sym, tok) in enumerate(tokens.items()):
        status["msg"] = f"Downloading {sym} ({i + 1}/{len(tokens)})"
        rows, cur = {}, start
        while cur < end:
            nxt = min(cur + timedelta(days=90), end)
            for d in kite.historical_data(tok, cur, nxt, "5minute"):
                rows[d["date"].isoformat()] = [d["date"].isoformat(), d["open"], d["high"], d["low"], d["close"], d["volume"]]
            cur = nxt
            time.sleep(0.4)  # Kite allows about 3 history requests per second
        with open(_path(cfg, sym), "w") as f:
            json.dump(sorted(rows.values()), f)


def load(cfg, symbols):
    out = {}
    for sym in symbols:
        p = _path(cfg, sym)
        if not os.path.exists(p):
            continue
        cs = []
        for ts, o, h, l, c, v in json.load(open(p)):
            t = datetime.fromisoformat(ts).astimezone(IST)
            if cfg.or_start <= t.time() <= datetime(2000, 1, 1, 15, 25).time():
                cs.append({"start": t, "o": o, "h": h, "l": l, "c": c, "v": v})
        if cs:
            out[sym] = cs
    return out


def _exit_px(tr, c, t, cfg):
    buy = tr["side"] == "BUY"
    stop_hit = c["l"] <= tr["stop"] if buy else c["h"] >= tr["stop"]
    tgt_hit = c["h"] >= tr["target"] if buy else c["l"] <= tr["target"]
    if stop_hit:
        gap = c["o"] < tr["stop"] if buy else c["o"] > tr["stop"]
        return (c["o"] if gap else tr["stop"]), "stop"
    if tgt_hit:
        return tr["target"], "target"
    if t.time() >= cfg.square_off:
        return c["o"], "eod"
    return None


def run(cfg, candles, factory, slippage_bps=2.0):
    risk, slip = Risk(cfg), slippage_bps / 10000.0
    by_day = defaultdict(lambda: defaultdict(dict))
    for sym, cs in candles.items():
        for c in cs:
            by_day[c["start"].date()][c["start"]][sym] = c
    trades = []

    def close(tr, px, reason, day):
        buy = tr["side"] == "BUY"
        px = px * (1 - slip if buy else 1 + slip)
        sign = 1 if buy else -1
        gross = (px - tr["entry"]) * tr["qty"] * sign
        cost = trade_cost(tr["side"], tr["qty"], tr["entry"], px)
        trades.append({"day": str(day), "symbol": tr["symbol"], "side": tr["side"], "qty": tr["qty"],
                       "entry": round(tr["entry"], 2), "exit": round(px, 2), "gross": round(gross, 2),
                       "cost": round(cost, 2), "net": round(gross - cost, 2), "reason": reason})
        return gross

    for day in sorted(by_day):
        strat, open_, done, last, realized, killed = factory(cfg), {}, set(), {}, 0.0, False
        for t in sorted(by_day[day]):
            for sym, c in by_day[day][t].items():
                last[sym] = c["c"]
                tr = open_.get(sym)
                if tr:
                    res = _exit_px(tr, c, t, cfg)
                    if res:
                        realized += close(tr, res[0], res[1], day)
                        del open_[sym]
                sig = strat.on_candle(sym, c)
                close_t = (t + timedelta(minutes=cfg.candle_minutes)).time()
                if (sig and not killed and sym not in done and cfg.or_end <= close_t < cfg.no_new_entry
                        and len(open_) < cfg.max_open_positions):
                    done.add(sym)
                    qty = risk.size(sig.entry, sig.stop)
                    if qty > 0:
                        buy = sig.side == "BUY"
                        open_[sym] = {"symbol": sym, "side": sig.side, "qty": qty, "stop": sig.stop,
                                      "target": sig.target, "entry": sig.entry * (1 + slip if buy else 1 - slip)}
            if not killed:
                unreal = sum((last[s] - o["entry"]) * o["qty"] * (1 if o["side"] == "BUY" else -1) for s, o in open_.items())
                if risk.breached(realized + unreal):
                    for s, o in list(open_.items()):
                        realized += close(o, last[s], "kill-switch", day)
                    open_.clear()
                    killed = True
        for s, o in list(open_.items()):
            close(o, last[s], "eod", day)
    return trades


def summarize(trades, days):
    def block(ts, ds):
        n = len(ts)
        wins = [x["net"] for x in ts if x["net"] > 0]
        losses = [x["net"] for x in ts if x["net"] <= 0]
        daily = defaultdict(float)
        for x in ts:
            daily[x["day"]] += x["net"]
        cum = peak = dd = 0.0
        for d in sorted(daily):
            cum += daily[d]
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
        return {"days": len(ds), "trades": n, "win_rate": round(len(wins) / n * 100, 1) if n else 0,
                "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
                "avg_trade": round(sum(x["net"] for x in ts) / n, 2) if n else 0,
                "net": round(sum(x["net"] for x in ts), 2), "gross": round(sum(x["gross"] for x in ts), 2),
                "costs": round(sum(x["cost"] for x in ts), 2), "max_drawdown": round(dd, 2)}
    days = sorted(days)
    cut = int(len(days) * 0.7)
    a, b = set(days[:cut]), set(days[cut:])
    return {"all": block(trades, days),
            "first_70": block([t for t in trades if t["day"] in a], a),
            "last_30": block([t for t in trades if t["day"] in b], b)}


def backtest(cfg, symbols, factory, slippage_bps=2.0):
    candles = load(cfg, symbols)
    if not candles:
        return None
    trades = run(cfg, candles, factory, slippage_bps)
    days = {str(c["start"].date()) for cs in candles.values() for c in cs}
    return {"symbols": sorted(candles), "summary": summarize(trades, days), "trades": trades[-40:]}
