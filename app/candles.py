class CandleBuilder:
    """Builds N-minute candles from ticks. A candle is returned when the next bucket's first tick arrives."""

    def __init__(self, minutes: int):
        self.n = minutes
        self.cur = {}

    def update(self, symbol, price, ts):
        start = ts.replace(minute=ts.minute - ts.minute % self.n, second=0, microsecond=0)
        c = self.cur.get(symbol)
        if c is None or start > c["start"]:
            self.cur[symbol] = {"start": start, "o": price, "h": price, "l": price, "c": price}
            return c  # previous candle, now closed (None for the first tick)
        c["h"] = max(c["h"], price)
        c["l"] = min(c["l"], price)
        c["c"] = price
        return None
