import tempfile
from datetime import datetime, timedelta

from app.broker import PaperBroker
from app.clock import IST
from app.config import load
from app.engine import Engine
from app.store import Store


class Clock:
    def __init__(self):
        self.t = datetime(2026, 11, 16, 9, 15, 0, tzinfo=IST)

    def __call__(self):
        return self.t


def build(monkeypatch=None, **env):
    import os
    os.environ["DATA_DIR"] = tempfile.mkdtemp()
    for k, v in env.items():
        os.environ[k] = v
    cfg = load()
    clk = Clock()
    store = Store(cfg.data_dir)
    eng = Engine(cfg, store, PaperBroker(slippage_bps=0), now_fn=clk)
    return eng, clk, store


def feed(eng, clk, sym, minute_prices):
    """minute_prices: list of (hh, mm, price); one tick per entry."""
    for h, m, p in minute_prices:
        clk.t = clk.t.replace(hour=h, minute=m, second=0)
        eng.on_tick(sym, p, clk.t)


def opening_range(eng, clk, sym="TCS"):
    feed(eng, clk, sym, [(9, 15, 100), (9, 17, 100.4), (9, 20, 99.8), (9, 24, 100.3), (9, 25, 100), (9, 29, 100.2)])


def settle(eng):
    eng._pool.shutdown(wait=True)
    from concurrent.futures import ThreadPoolExecutor
    eng._pool = ThreadPoolExecutor(max_workers=1)


def test_breakout_entry_stop_and_target():
    eng, clk, store = build()
    eng.arm()
    opening_range(eng, clk)
    feed(eng, clk, "TCS", [(9, 30, 100.3), (9, 34, 100.6), (9, 35, 100.7)])  # 09:30 candle closes above 101 -> BUY
    settle(eng)
    assert "TCS" in eng.trades
    tr = eng.trades["TCS"]
    assert tr["side"] == "BUY" and tr["stop"] == 99.8
    assert tr["qty"] == 625  # Rs500 risk budget / (100.6 signal close - 99.8 stop)
    feed(eng, clk, "TCS", [(9, 40, tr["target"] + 0.1)])
    settle(eng)
    assert "TCS" not in eng.trades
    row = store.trades_for("2026-11-16")[0]
    assert row["reason"] == "target" and row["pnl"] > 0


def test_stop_loss_fills_via_watchdog():
    eng, clk, store = build()
    eng.arm()
    opening_range(eng, clk)
    feed(eng, clk, "TCS", [(9, 30, 100.3), (9, 34, 100.6), (9, 35, 100.7)])
    settle(eng)
    feed(eng, clk, "TCS", [(9, 41, 99.0)])  # gaps through trigger and limit, so SL-limit stays open
    assert eng.trades["TCS"]["state"] == "open"
    feed(eng, clk, "TCS", [(9, 42, 99.6)])  # inside limit -> fills
    eng.watchdog_once()
    assert "TCS" not in eng.trades
    assert store.trades_for("2026-11-16")[0]["reason"] == "stop"


def test_kill_switch_locks_and_survives_restart():
    eng, clk, store = build(CAPITAL="100000", MAX_DAILY_LOSS_PCT="1.5")
    eng.arm()
    opening_range(eng, clk)
    feed(eng, clk, "TCS", [(9, 30, 100.3), (9, 34, 100.6), (9, 35, 100.7)])
    settle(eng)
    # Crash far below the stop and keep the SL-limit unfilled: unrealised loss breaches 1.5%.
    feed(eng, clk, "TCS", [(9, 50, 60.0)])
    eng.watchdog_once()
    assert eng.killed and store.lock_info("2026-11-16")
    assert eng.broker.positions() == []
    ok, msg = eng.arm()
    assert not ok
    # "Restart": new engine on same DB must still be locked.
    eng2 = Engine(eng.cfg, store, PaperBroker(), now_fn=clk)
    assert eng2.killed and not eng2.arm()[0]


def test_no_entry_when_disarmed_or_after_cutoff():
    eng, clk, store = build()
    opening_range(eng, clk)
    feed(eng, clk, "TCS", [(9, 30, 100.3), (9, 34, 100.6), (9, 35, 100.7)])
    settle(eng)
    assert eng.trades == {}
    eng2, clk2, _ = build()
    eng2.arm()
    opening_range(eng2, clk2, "INFY")
    feed(eng2, clk2, "INFY", [(14, 31, 100.7), (14, 36, 100.8), (14, 41, 100.9)])
    settle(eng2)
    assert eng2.trades == {}


def test_eod_squareoff():
    eng, clk, store = build()
    eng.arm()
    opening_range(eng, clk)
    feed(eng, clk, "TCS", [(9, 30, 100.3), (9, 34, 100.6), (9, 35, 100.7)])
    settle(eng)
    clk.t = clk.t.replace(hour=15, minute=11)
    eng.watchdog_once()
    assert eng.broker.positions() == [] and eng.eod_done and not eng.armed
