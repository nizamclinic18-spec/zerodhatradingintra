import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .candles import CandleBuilder
from .clock import now_ist
from .risk import Risk
from .registry import make_strategy

log = logging.getLogger("engine")
TICK = 0.05


def rt(p: float) -> float:
    return round(round(p / TICK) * TICK, 2)


class Engine:
    def __init__(self, cfg, store, broker, now_fn=now_ist):
        self.cfg, self.store, self.broker, self.now = cfg, store, broker, now_fn
        self.risk = Risk(cfg)
        self.strategy = make_strategy(cfg)
        self.candles = CandleBuilder(cfg.candle_minutes)
        self.ltp = {}
        self.trades = {}          # symbol -> open trade dict
        self.done = set()         # symbols already attempted today (one attempt each)
        self.pending = set()
        self.armed = False
        self.killed = False
        self.eod_done = False
        self.day_pnl = 0.0
        self.positions = []
        self.last_tick = None
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=1)   # order calls never block the tick thread
        self._stop = threading.Event()
        self._thread = None
        lk = store.lock_info(self.today())
        if lk:
            self.killed = True
            self.event("CRITICAL", f"Locked for today: {lk['reason']}")
        # Adopt positions that already exist (e.g. after a restart) so risk and EOD cover them.
        try:
            for p in broker.positions():
                self.done.add(p["symbol"])
                self.event("WARN", f"Existing position found at start: {p['symbol']} qty {p['qty']} (not managed by strategy; covered by loss limit and EOD square-off)")
        except Exception as e:
            self.event("WARN", f"Could not read positions at start: {e}")

    # ---------- helpers ----------
    def today(self):
        return self.now().date().isoformat()

    def event(self, level, msg):
        getattr(log, "error" if level in ("CRITICAL", "ERROR") else "info")(msg)
        try:
            self.store.log(self.now().strftime("%H:%M:%S"), level, msg)
        except Exception:
            pass

    def start(self):
        self._thread = threading.Thread(target=self._watchdog_loop, daemon=True, name="watchdog")
        self._thread.start()

    def stop(self):
        self._stop.set()

    # ---------- controls ----------
    def arm(self):
        with self._lock:
            if self.killed:
                return False, "Locked for today after the kill-switch. It clears tomorrow."
            if self.eod_done:
                return False, "Session already squared off for today."
            self.armed = True
        self.event("INFO", f"ARMED in {self.cfg.mode.upper()} mode")
        return True, "armed"

    def disarm(self):
        self.armed = False
        self.event("INFO", "DISARMED: no new entries. Open positions keep their stop-loss.")

    # ---------- ticks ----------
    def on_tick(self, symbol, ltp, ts=None):
        ts = ts or self.now()
        self.ltp[symbol] = ltp
        self.last_tick = ts
        self.broker.on_tick(symbol, ltp)
        candle = self.candles.update(symbol, ltp, ts)
        if candle:
            sig = self.strategy.on_candle(symbol, candle)
            if sig and self.armed and not self.killed:
                self.event("INFO", f"Signal {sig.side} {sig.symbol} entry {sig.entry:.2f} stop {sig.stop:.2f} target {sig.target:.2f}")
                self._pool.submit(self._safe, self._enter, sig)
        tr = self.trades.get(symbol)
        if tr and tr["state"] == "open":
            hit = ltp >= tr["target"] if tr["side"] == "BUY" else ltp <= tr["target"]
            if hit:
                tr["state"] = "closing"
                self._pool.submit(self._safe, self._close_trade, symbol, "target")

    def _safe(self, fn, *a):
        try:
            fn(*a)
        except Exception as e:
            self.event("ERROR", f"{fn.__name__} failed: {e}")

    # ---------- entry ----------
    def _can_enter(self, sym):
        t = self.now().time()
        if not self.armed or self.killed or self.eod_done:
            return False
        if self.store.lock_info(self.today()):
            return False
        if not (self.cfg.or_end <= t < self.cfg.no_new_entry):
            return False
        return sym not in self.done and sym not in self.pending

    def _await_fill(self, oid, timeout=6.0):
        end = time.time() + timeout
        st = self.broker.get_order(oid)
        while st["status"] not in ("COMPLETE", "REJECTED", "CANCELLED") and time.time() < end:
            time.sleep(0.4)
            st = self.broker.get_order(oid)
        if st["status"] not in ("COMPLETE", "REJECTED", "CANCELLED"):
            try:
                self.broker.cancel(oid)
            except Exception:
                pass
            st = self.broker.get_order(oid)
        return st

    def _enter(self, sig):
        sym = sig.symbol
        with self._lock:
            if not self._can_enter(sym):
                return
            if len(self.trades) + len(self.pending) >= self.cfg.max_open_positions:
                self.event("INFO", f"Max open positions reached, skipping {sym}")
                return
            self.pending.add(sym)
            self.done.add(sym)
        try:
            if len(self.broker.positions()) >= self.cfg.max_open_positions:
                self.event("INFO", f"Broker already shows max positions, skipping {sym}")
                return
            qty = self.risk.size(sig.entry, sig.stop)
            if qty <= 0:
                self.event("WARN", f"Size is 0 for {sym}; stop too wide for the risk budget")
                return
            exit_side = "SELL" if sig.side == "BUY" else "BUY"
            oid = self.broker.place_market(sym, sig.side, qty)
            st = self._await_fill(oid)
            filled, avg = st["filled"], st["avg"]
            if filled <= 0:
                self.event("WARN", f"Entry for {sym} not filled ({st['status']}); no position taken")
                return
            self.event("INFO", f"ENTRY {sig.side} {filled} {sym} @ {avg:.2f}")
            # Fill-confirmed, so the stop is sized to what actually filled.
            gapped = (sig.side == "BUY" and avg <= sig.stop) or (sig.side == "SELL" and avg >= sig.stop)
            trade = {"symbol": sym, "side": sig.side, "qty": filled, "entry": avg, "stop": sig.stop,
                     "target": sig.target, "sl_id": None, "state": "open",
                     "opened": self.now().strftime("%H:%M:%S")}
            self.trades[sym] = trade
            if gapped:
                self.event("WARN", f"{sym} filled beyond its stop; exiting immediately")
                self._close_trade(sym, "filled-beyond-stop")
                return
            trig = rt(sig.stop)
            buf = 0.003
            limit = rt(trig * (1 - buf)) if exit_side == "SELL" else rt(trig * (1 + buf))
            try:
                trade["sl_id"] = self.broker.place_sl(sym, exit_side, filled, trig, limit)
                self.event("INFO", f"STOP placed {exit_side} {filled} {sym} trigger {trig:.2f} limit {limit:.2f}")
            except Exception as e:
                self.event("CRITICAL", f"Stop-loss placement FAILED for {sym} ({e}). Flattening now.")
                self._close_trade(sym, "no-stop-flatten")
        finally:
            with self._lock:
                self.pending.discard(sym)

    # ---------- exit ----------
    def _finalize(self, tr, exit_px, reason):
        sign = 1 if tr["side"] == "BUY" else -1
        pnl = round((exit_px - tr["entry"]) * tr["qty"] * sign, 2)
        now = self.now()
        self.store.add_trade(now.date().isoformat(), tr["symbol"], tr["side"], tr["qty"], tr["entry"],
                             exit_px, pnl, reason, tr["opened"], now.strftime("%H:%M:%S"))
        self.event("INFO", f"CLOSED {tr['symbol']} {reason} @ {exit_px:.2f}  gross P&L {pnl:+.2f}")
        self.trades.pop(tr["symbol"], None)

    def _close_trade(self, sym, reason):
        tr = self.trades.get(sym)
        if not tr:
            return
        tr["state"] = "closing"
        remaining, sl_fill_qty, sl_fill_val = tr["qty"], 0, 0.0
        if tr["sl_id"]:
            st = self.broker.get_order(tr["sl_id"])
            if st["status"] not in ("COMPLETE", "REJECTED", "CANCELLED"):
                try:
                    self.broker.cancel(tr["sl_id"])
                except Exception:
                    pass
                st = self.broker.get_order(tr["sl_id"])  # it may have filled while cancelling
            sl_fill_qty, sl_fill_val = st["filled"], st["filled"] * st["avg"]
            remaining = tr["qty"] - st["filled"]
        exit_side = "SELL" if tr["side"] == "BUY" else "BUY"
        mkt_px = 0.0
        if remaining > 0:
            oid = self.broker.place_market(sym, exit_side, remaining)
            st2 = self._await_fill(oid)
            if st2["filled"] < remaining:
                self.event("CRITICAL", f"Exit for {sym} incomplete ({st2['filled']}/{remaining}). Check broker NOW.")
            mkt_px = st2["avg"]
            sl_fill_qty += st2["filled"]
            sl_fill_val += st2["filled"] * mkt_px
        exit_px = sl_fill_val / sl_fill_qty if sl_fill_qty else self.ltp.get(sym, tr["entry"])
        self._finalize(tr, round(exit_px, 2), reason)

    def square_off_all(self, attempts=3):
        """Flatten every MIS position, one by one, so a single failure cannot strand the rest."""
        for i in range(attempts):
            try:
                pos = self.broker.positions()
            except Exception as e:
                self.event("ERROR", f"positions() failed during square-off: {e}")
                time.sleep(1)
                continue
            if not pos:
                return True
            for p in pos:
                try:
                    side = "SELL" if p["qty"] > 0 else "BUY"
                    self.broker.place_market(p["symbol"], side, abs(p["qty"]))
                    self.event("INFO", f"Square-off {side} {abs(p['qty'])} {p['symbol']}")
                except Exception as e:
                    self.event("ERROR", f"Square-off failed for {p['symbol']}: {e}")
            time.sleep(1.5)
        try:
            left = self.broker.positions()
        except Exception:
            left = None
        if left:
            self.event("CRITICAL", f"Positions still open after square-off: {[p['symbol'] for p in left]}. Close manually in Kite.")
            return False
        return True

    def _flush_trades(self, reason):
        for sym, tr in list(self.trades.items()):
            self._finalize(tr, self.ltp.get(sym, tr["entry"]), reason)

    def kill_switch(self, reason, lock=True):
        with self._lock:
            if self.killed and self.store.lock_info(self.today()):
                return
            self.killed = True
            self.armed = False
        self.event("CRITICAL", f"KILL-SWITCH: {reason}")
        if lock:
            self.store.lock_today(self.today(), reason, self.now().isoformat())  # lock first, trade after
        try:
            n = self.broker.cancel_all_open()
            self.event("INFO", f"Cancelled {n} open orders")
        except Exception as e:
            self.event("ERROR", f"cancel_all_open failed: {e}")
        self.square_off_all()
        self._flush_trades("kill-switch")

    # ---------- watchdog ----------
    def _watchdog_loop(self):
        while not self._stop.wait(self.cfg.poll_seconds):
            try:
                self.watchdog_once()
            except Exception as e:
                self.event("WARN", f"watchdog error: {e}")

    def watchdog_once(self):
        now = self.now()
        if self.killed:
            return
        if now.time() >= self.cfg.square_off and not self.eod_done:
            self.eod_done, self.armed = True, False
            self.event("INFO", "End-of-day square-off")
            self.broker.cancel_all_open()
            self.square_off_all()
            self._flush_trades("eod")
            return
        self.positions = self.broker.positions()
        self.day_pnl = self.broker.day_pnl()
        if self.risk.breached(self.day_pnl):
            self.kill_switch(f"Daily loss limit hit: P&L {self.day_pnl:.2f} <= -{self.cfg.loss_limit:.2f}")
            return
        for sym, tr in list(self.trades.items()):
            if tr["state"] != "open" or not tr["sl_id"]:
                continue
            st = self.broker.get_order(tr["sl_id"])
            if st["status"] == "COMPLETE":
                tr["state"] = "closing"
                self._finalize(tr, st["avg"], "stop")
            elif st["status"] in ("REJECTED", "CANCELLED"):
                self.event("CRITICAL", f"Stop for {sym} is {st['status']}; position unprotected, flattening")
                self._pool.submit(self._safe, self._close_trade, sym, "stop-lost")

    # ---------- UI ----------
    def snapshot(self):
        now = self.now()
        lk = self.store.lock_info(self.today())
        return {
            "now": now.strftime("%H:%M:%S"), "mode": self.cfg.mode, "armed": self.armed,
            "killed": self.killed, "lock": lk, "eod_done": self.eod_done,
            "day_pnl": self.day_pnl, "loss_limit": self.cfg.loss_limit, "capital": self.cfg.capital,
            "positions": self.positions,
            "open_trades": [{k: v for k, v in t.items() if k != "sl_id"} for t in self.trades.values()],
            "closed_trades": self.store.trades_for(self.today()),
            "watch": [{"symbol": s, "ltp": self.ltp.get(s), "range": self.strategy.levels(s)} for s in self.cfg.watchlist],
            "last_tick_age": None if not self.last_tick else round((now - self.last_tick).total_seconds(), 1),
            "events": self.store.recent_events(60),
            "rules": {"risk_per_trade_pct": self.cfg.risk_per_trade_pct, "max_open": self.cfg.max_open_positions,
                      "no_new_entry": self.cfg.no_new_entry.strftime("%H:%M"),
                      "square_off": self.cfg.square_off.strftime("%H:%M")},
        }
