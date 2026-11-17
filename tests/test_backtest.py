import os
import tempfile
from datetime import datetime, timedelta

from app.backtest import run, summarize
from app.clock import IST
from app.config import load
from app.costs import trade_cost
from app.registry import make_strategy


def cfg():
    os.environ["DATA_DIR"] = tempfile.mkdtemp()
    return load()


def day_candles(day, prices):
    """prices: list of (o,h,l,c) from 09:15 in 5-min steps."""
    t = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
    return [{"start": t + timedelta(minutes=5 * i), "o": o, "h": h, "l": l, "c": c, "v": 1000}
            for i, (o, h, l, c) in enumerate(prices)]


def test_costs_are_positive_and_sensible():
    c = trade_cost("BUY", 100, 1000.0, 1010.0)   # Rs1,00,000 buy / Rs1,01,000 sell
    assert 40 < c < 120


def test_breakout_trade_hits_target_and_pays_costs():
    cf = cfg()
    base = [(100, 100.4, 99.8, 100.2)] * 3 + [(100.2, 100.3, 100.0, 100.2)] * 3     # OR 99.8-100.4, candles 09:15-09:45
    breakout = [(100.2, 100.8, 100.2, 100.7)]                                        # 09:30 candle closes above range... (index 3 in list)
    rally = [(100.7, 101.0, 100.6, 100.9), (100.9, 102.8, 100.9, 102.0)]
    cs = {"TCS": day_candles(datetime(2026, 11, 2).date(), base[:3] + breakout + rally + [(102, 102, 101.9, 102)] * 60)}
    trades = run(cf, cs, lambda c: make_strategy(c, "orb"), slippage_bps=0)
    assert trades and trades[0]["reason"] == "target" and trades[0]["gross"] > 0
    assert trades[0]["net"] < trades[0]["gross"]
    s = summarize(trades, {"2026-11-02"})
    assert s["all"]["trades"] == 1 and s["all"]["costs"] > 0
