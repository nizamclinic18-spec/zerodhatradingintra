"""Broker layer. Engine talks only to this interface, so paper and live behave the same.

place_market(symbol, side, qty) -> order_id
place_sl(symbol, side, qty, trigger, limit) -> order_id
get_order(order_id) -> {"status", "filled", "avg"}   status: OPEN|COMPLETE|REJECTED|CANCELLED
cancel(order_id), cancel_all_open()
positions() -> [{"symbol","qty","avg","ltp","pnl"}]  (non-zero MIS only)
day_pnl() -> float (realized + unrealized, MIS)
"""
import json
import threading

OPEN_STATES = ("OPEN", "TRIGGER PENDING", "VALIDATION PENDING", "MODIFY PENDING", "PUT ORDER REQ RECEIVED")


class LiveBroker:
    def __init__(self, kite):
        self.k = kite

    def place_market(self, symbol, side, qty):
        return self.k.place_order(
            variety=self.k.VARIETY_REGULAR, exchange="NSE", tradingsymbol=symbol,
            transaction_type=side, quantity=int(qty), product=self.k.PRODUCT_MIS,
            order_type=self.k.ORDER_TYPE_MARKET, tag="KB",
        )

    def place_sl(self, symbol, side, qty, trigger, limit):
        return self.k.place_order(
            variety=self.k.VARIETY_REGULAR, exchange="NSE", tradingsymbol=symbol,
            transaction_type=side, quantity=int(qty), product=self.k.PRODUCT_MIS,
            order_type=self.k.ORDER_TYPE_SL, trigger_price=trigger, price=limit, tag="KB",
        )

    def get_order(self, order_id):
        last = self.k.order_history(order_id)[-1]
        return {"status": last["status"], "filled": int(last.get("filled_quantity") or 0),
                "avg": float(last.get("average_price") or 0.0)}

    def cancel(self, order_id):
        self.k.cancel_order(variety=self.k.VARIETY_REGULAR, order_id=order_id)

    def cancel_all_open(self):
        """Cancels only MIS orders so unrelated orders in the account are untouched."""
        n = 0
        for o in self.k.orders():
            if o.get("product") == "MIS" and o["status"] in OPEN_STATES:
                try:
                    self.k.cancel_order(variety=o["variety"], order_id=o["order_id"])
                    n += 1
                except Exception:
                    pass
        return n

    def positions(self):
        out = []
        for p in self.k.positions().get("day", []):
            if p["product"] == "MIS" and p["quantity"] != 0:
                out.append({"symbol": p["tradingsymbol"], "qty": p["quantity"], "avg": p["average_price"],
                            "ltp": p["last_price"], "pnl": p["pnl"]})
        return out

    def day_pnl(self):
        return float(sum(p.get("pnl", 0.0) for p in self.k.positions().get("day", []) if p["product"] == "MIS"))

    def on_tick(self, symbol, ltp):
        pass


class PaperBroker:
    """Simulated fills on live ticks. Market orders fill at LTP +/- slippage.
    SL-limit orders trigger at the trigger price but only fill if price is still
    inside the limit, the same gap-through risk the real exchange has."""

    def __init__(self, slippage_bps: float = 2.0):
        self.slip = slippage_bps / 10000.0
        self.ltp = {}
        self.orders = {}
        self.pos = {}
        self._n = 0
        self._l = threading.RLock()

    def _fill(self, o, price):
        o.update(status="COMPLETE", filled=o["qty"], avg=round(price, 2))
        p = self.pos.setdefault(o["symbol"], {"qty": 0, "avg": 0.0, "realized": 0.0})
        signed = o["qty"] if o["side"] == "BUY" else -o["qty"]
        if p["qty"] == 0 or (p["qty"] > 0) == (signed > 0):
            total = abs(p["qty"]) + abs(signed)
            p["avg"] = (p["avg"] * abs(p["qty"]) + price * abs(signed)) / total
            p["qty"] += signed
        else:
            closing = min(abs(signed), abs(p["qty"]))
            p["realized"] += (price - p["avg"]) * closing * (1 if p["qty"] > 0 else -1)
            p["qty"] += signed
            if p["qty"] != 0 and (p["qty"] > 0) == (signed > 0):
                p["avg"] = price
            if p["qty"] == 0:
                p["avg"] = 0.0

    def place_market(self, symbol, side, qty):
        with self._l:
            px = self.ltp.get(symbol)
            if not px:
                raise RuntimeError(f"no price for {symbol}")
            self._n += 1
            oid = f"P{self._n}"
            o = {"id": oid, "symbol": symbol, "side": side, "qty": int(qty), "type": "MARKET",
                 "status": "OPEN", "filled": 0, "avg": 0.0}
            self.orders[oid] = o
            self._fill(o, px * (1 + self.slip if side == "BUY" else 1 - self.slip))
            return oid

    def place_sl(self, symbol, side, qty, trigger, limit):
        with self._l:
            self._n += 1
            oid = f"P{self._n}"
            self.orders[oid] = {"id": oid, "symbol": symbol, "side": side, "qty": int(qty), "type": "SL",
                                "trigger": trigger, "limit": limit, "status": "OPEN", "filled": 0,
                                "avg": 0.0, "triggered": False}
            return oid

    def get_order(self, order_id):
        with self._l:
            o = self.orders[order_id]
            return {"status": o["status"], "filled": o["filled"], "avg": o["avg"]}

    def cancel(self, order_id):
        with self._l:
            o = self.orders[order_id]
            if o["status"] != "OPEN":
                raise RuntimeError("order not open")
            o["status"] = "CANCELLED"

    def cancel_all_open(self):
        with self._l:
            n = 0
            for o in self.orders.values():
                if o["status"] == "OPEN":
                    o["status"] = "CANCELLED"
                    n += 1
            return n

    def on_tick(self, symbol, ltp):
        with self._l:
            self.ltp[symbol] = ltp
            for o in self.orders.values():
                if o["symbol"] != symbol or o["status"] != "OPEN" or o["type"] != "SL":
                    continue
                if not o["triggered"]:
                    o["triggered"] = ltp <= o["trigger"] if o["side"] == "SELL" else ltp >= o["trigger"]
                if o["triggered"]:
                    inside = ltp >= o["limit"] if o["side"] == "SELL" else ltp <= o["limit"]
                    if inside:
                        self._fill(o, ltp)

    def snapshot(self):
        with self._l:
            return json.loads(json.dumps({"orders": self.orders, "pos": self.pos, "n": self._n}))

    def restore(self, d):
        with self._l:
            self.orders, self.pos, self._n = d["orders"], d["pos"], d["n"]

    def positions(self):
        with self._l:
            out = []
            for s, p in self.pos.items():
                if p["qty"] != 0:
                    ltp = self.ltp.get(s, p["avg"])
                    out.append({"symbol": s, "qty": p["qty"], "avg": round(p["avg"], 2), "ltp": ltp,
                                "pnl": round(p["realized"] + (ltp - p["avg"]) * p["qty"], 2)})
            return out

    def day_pnl(self):
        with self._l:
            total = 0.0
            for s, p in self.pos.items():
                ltp = self.ltp.get(s, p["avg"])
                total += p["realized"] + ((ltp - p["avg"]) * p["qty"] if p["qty"] else 0.0)
            return round(total, 2)
