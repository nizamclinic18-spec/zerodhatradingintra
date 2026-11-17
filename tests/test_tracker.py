from app.broker import PaperBroker
from app.engine import Engine
from test_engine import build, feed, opening_range, settle


def test_tracker_alerts_but_never_trades():
    eng, clk, store = build()
    assert eng.track_add("SBIN")                       # SBIN is not on the bot's watchlist
    opening_range(eng, clk, "SBIN")
    feed(eng, clk, "SBIN", [(9, 30, 100.3), (9, 34, 100.6), (9, 35, 100.7)])
    settle(eng)
    assert len(eng.alerts) == 1
    a = eng.alerts[0]
    assert a["symbol"] == "SBIN" and a["side"] == "BUY" and a["stop"] == 99.8 and a["qty"] == 625
    assert eng.trades == {} and eng.broker.orders == {}      # no orders, ever, even when armed
    eng.arm()
    feed(eng, clk, "SBIN", [(9, 41, 100.0)])
    assert eng.broker.orders == {}
    snap = eng.snapshot()
    assert snap["tracker"][0]["symbol"] == "SBIN" and snap["alerts"][0]["symbol"] == "SBIN"


def test_tracker_does_not_disturb_bot_and_survives_restart():
    eng, clk, store = build()
    eng.arm()
    eng.track_add("SBIN")
    for sym in ("TCS", "SBIN"):
        opening_range(eng, clk, sym)
        feed(eng, clk, sym, [(9, 30, 100.3), (9, 34, 100.6), (9, 35, 100.7)])
    settle(eng)
    assert "TCS" in eng.trades and "SBIN" not in eng.trades   # bot trades only its watchlist
    assert [a["symbol"] for a in eng.alerts] == ["SBIN"]
    eng2 = Engine(eng.cfg, store, PaperBroker(slippage_bps=0), now_fn=clk)
    assert eng2.tracked == ["SBIN"] and len(eng2.alerts) == 1


def test_track_remove_and_limit():
    eng, clk, store = build()
    for i in range(30):
        assert eng.track_add(f"S{i}")
    assert not eng.track_add("EXTRA")
    eng.track_remove("S0")
    assert "S0" not in eng.tracked and eng.track_add("EXTRA")
