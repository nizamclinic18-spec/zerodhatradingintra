import logging

from kiteconnect import KiteTicker

log = logging.getLogger("ticker")


class TickerService:
    def __init__(self, api_key, access_token, token_map, engine):
        self.map = token_map                       # symbol -> instrument_token
        self.rev = {v: k for k, v in token_map.items()}
        self.engine = engine
        self.kws = KiteTicker(api_key, access_token)
        self.kws.on_ticks = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close = lambda ws, c, r: self.engine.event("WARN", f"Ticker closed: {c} {r}")
        self.kws.on_error = lambda ws, c, r: self.engine.event("ERROR", f"Ticker error: {c} {r}")
        self.kws.on_reconnect = lambda ws, n: self.engine.event("WARN", f"Ticker reconnecting (try {n})")

    def _on_connect(self, ws, _resp):
        toks = list(self.map.values())
        ws.subscribe(toks)
        ws.set_mode(ws.MODE_LTP, toks)
        self.engine.event("INFO", f"Ticker connected, {len(toks)} symbols")

    def _on_ticks(self, ws, ticks):
        for t in ticks:
            sym = self.rev.get(t["instrument_token"])
            if sym and t.get("last_price"):
                self.engine.on_tick(sym, float(t["last_price"]))

    def start(self):
        self.kws.connect(threaded=True)   # KiteTicker auto-reconnects

    def stop(self):
        try:
            self.kws.close()
        except Exception:
            pass
