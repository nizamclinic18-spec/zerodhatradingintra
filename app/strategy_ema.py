from .strategy import Signal


class EMACrossStrategy:
    """Fast EMA crosses slow EMA. Stop = extreme of the last few candles."""

    def __init__(self, cfg, fast=5, slow=13, swing=3):
        self.cfg, self.fast, self.slow, self.swing = cfg, fast, slow, swing
        self.st = {}
        self.notes = []

    def levels(self, symbol):
        return None

    def on_candle(self, symbol, c):
        s = self.st.setdefault(symbol, {"f": None, "s": None, "pf": None, "ps": None, "lo": [], "hi": [], "n": 0})
        kf, ks, px = 2 / (self.fast + 1), 2 / (self.slow + 1), c["c"]
        s["pf"], s["ps"] = s["f"], s["s"]
        s["f"] = px if s["f"] is None else px * kf + s["f"] * (1 - kf)
        s["s"] = px if s["s"] is None else px * ks + s["s"] * (1 - ks)
        s["lo"] = (s["lo"] + [c["l"]])[-self.swing:]
        s["hi"] = (s["hi"] + [c["h"]])[-self.swing:]
        s["n"] += 1
        if s["n"] < self.slow or s["pf"] is None:
            return None
        if s["pf"] <= s["ps"] and s["f"] > s["s"]:
            side, stop = "BUY", min(s["lo"])
        elif s["pf"] >= s["ps"] and s["f"] < s["s"]:
            side, stop = "SELL", max(s["hi"])
        else:
            return None
        risk = abs(px - stop)
        if risk <= 0 or risk / px * 100 > self.cfg.max_stop_pct:
            self.notes.append(f"{symbol} {side} crossover skipped: stop would be {risk / px * 100:.1f}% away, limit is {self.cfg.max_stop_pct:.1f}% (MAX_STOP_PCT)")
            return None
        target = px + self.cfg.target_r * risk if side == "BUY" else px - self.cfg.target_r * risk
        return Signal(symbol, side, px, stop, target)
