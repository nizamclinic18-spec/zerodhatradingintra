"""SAMPLE strategy: opening range breakout (ORB). It exists to prove the plumbing works.
It is NOT a proven edge. Paper trade it, then replace on_candle with your own logic."""
from dataclasses import dataclass


@dataclass
class Signal:
    symbol: str
    side: str      # BUY | SELL
    entry: float
    stop: float
    target: float


class ORBStrategy:
    def __init__(self, cfg):
        self.cfg = cfg
        self.range = {}   # symbol -> {"high","low","starts":set()}
        self.fired = set()
        self.notes = []       # skip reasons; the engine writes these to the Activity log
        self.warned = set()
        mins = (cfg.or_end.hour * 60 + cfg.or_end.minute) - (cfg.or_start.hour * 60 + cfg.or_start.minute)
        self.expected = max(mins // cfg.candle_minutes, 1)

    def levels(self, symbol):
        r = self.range.get(symbol)
        return None if not r else {"high": r["high"], "low": r["low"], "complete": len(r["starts"]) >= self.expected}

    def on_candle(self, symbol, c):
        t = c["start"].time()
        cfg = self.cfg
        if cfg.or_start <= t < cfg.or_end:
            r = self.range.setdefault(symbol, {"high": c["h"], "low": c["l"], "starts": set()})
            r["high"] = max(r["high"], c["h"])
            r["low"] = min(r["low"], c["l"])
            r["starts"].add(c["start"])
            return None
        r = self.range.get(symbol)
        if t < cfg.or_end or symbol in self.fired:
            return None
        # Bot started late or restarted => incomplete range => skip the symbol rather than guess.
        if not r or len(r["starts"]) < self.expected:
            if symbol not in self.warned:
                self.warned.add(symbol)
                seen = len(r["starts"]) if r else 0
                self.notes.append(f"{symbol} skipped for today: opening range incomplete ({seen}/{self.expected} candles seen; the bot started late or restarted)")
            return None
        close = c["c"]
        if close > r["high"]:
            side, stop = "BUY", r["low"]
        elif close < r["low"]:
            side, stop = "SELL", r["high"]
        else:
            return None
        self.fired.add(symbol)  # one attempt per symbol per day
        risk = abs(close - stop)
        if risk <= 0 or risk / close * 100 > cfg.max_stop_pct:
            self.notes.append(f"{symbol} {side} breakout skipped: stop would be {risk / close * 100:.1f}% away, limit is {cfg.max_stop_pct:.1f}% (MAX_STOP_PCT)")
            return None
        target = close + cfg.target_r * risk if side == "BUY" else close - cfg.target_r * risk
        return Signal(symbol, side, close, stop, target)
