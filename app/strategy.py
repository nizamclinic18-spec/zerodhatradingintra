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
        # Bot started late => incomplete range => skip the symbol rather than guess.
        if t < cfg.or_end or not r or len(r["starts"]) < self.expected or symbol in self.fired:
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
            return None
        target = close + cfg.target_r * risk if side == "BUY" else close - cfg.target_r * risk
        return Signal(symbol, side, close, stop, target)
