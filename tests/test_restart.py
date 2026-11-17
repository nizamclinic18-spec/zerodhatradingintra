from datetime import datetime

from app.broker import PaperBroker
from app.clock import IST
from app.engine import Engine
from test_engine import build, feed, opening_range, settle

BREAKOUT = [(9, 30, 100.3), (9, 34, 100.6), (9, 35, 100.7)]


def events(store):
    return [e["msg"] for e in store.recent_events(200)]


def test_restart_restores_armed_trade_and_stop():
    eng, clk, store = build()
    eng.arm()
    opening_range(eng, clk)
    feed(eng, clk, "TCS", BREAKOUT)
    settle(eng)
    assert "TCS" in eng.trades
    eng2 = Engine(eng.cfg, store, PaperBroker(slippage_bps=0), now_fn=clk)   # simulated restart
    assert eng2.armed and "TCS" in eng2.trades and "TCS" in eng2.done
    assert eng2.broker.positions()[0]["qty"] == 625
    feed(eng2, clk, "TCS", [(9, 42, 99.6)])      # stop triggers and fills
    eng2.watchdog_once()
    assert "TCS" not in eng2.trades
    assert store.trades_for("2026-11-16")[0]["reason"] == "stop"


def test_replay_rebuilds_range_but_does_not_chase():
    eng, clk, store = build()
    eng.arm()
    clk.t = clk.t.replace(hour=9, minute=50)

    def row(h, m, o, hi, lo, c):
        return {"date": datetime(2026, 11, 16, h, m, tzinfo=IST), "open": o, "high": hi, "low": lo, "close": c}

    rows = [row(9, 15, 100, 100.4, 99.8, 100.2), row(9, 20, 100.2, 100.3, 99.8, 100.0), row(9, 25, 100, 100.3, 99.9, 100.2),
            row(9, 30, 100.2, 100.8, 100.2, 100.7), row(9, 35, 100.7, 100.9, 100.6, 100.8)]
    eng.replay("TCS", rows)
    settle(eng)
    assert eng.trades == {}
    assert any("not chasing" in m for m in events(store))
    assert eng.strategy.levels("TCS")["complete"]


def test_skip_reasons_are_logged():
    eng, clk, store = build()
    eng.arm()
    feed(eng, clk, "TCS", [(9, 15, 100), (9, 17, 102), (9, 20, 99), (9, 24, 101), (9, 25, 100), (9, 29, 100.5),
                           (9, 30, 101), (9, 34, 102.5), (9, 35, 102.6)])
    settle(eng)
    assert any("stop would be" in m and "MAX_STOP_PCT" in m for m in events(store))
    eng2, clk2, store2 = build()
    eng2.arm()
    feed(eng2, clk2, "INFY", [(9, 40, 100), (9, 46, 100)])     # bot "started late": no opening range
    assert any("opening range incomplete" in m for m in events(store2))
