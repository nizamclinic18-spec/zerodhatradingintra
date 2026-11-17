"""Approximate Zerodha intraday equity (MIS) charges. Rates change: compare one
sample trade with Zerodha's own brokerage calculator and adjust the constants."""
BROKERAGE_PCT, BROKERAGE_CAP = 0.0003, 20.0   # lower of 0.03% or Rs20 per executed order
STT_SELL = 0.00025                             # on sell value
EXCH_TXN = 0.0000307                           # NSE transaction charge, on turnover
SEBI = 0.000001                                # Rs10 per crore
STAMP_BUY = 0.00003                            # on buy value
GST = 0.18                                     # on brokerage + exchange + SEBI


def trade_cost(side: str, qty: int, entry: float, exit_: float) -> float:
    buy_val = (entry if side == "BUY" else exit_) * qty
    sell_val = (exit_ if side == "BUY" else entry) * qty
    turnover = buy_val + sell_val
    brokerage = min(BROKERAGE_CAP, BROKERAGE_PCT * buy_val) + min(BROKERAGE_CAP, BROKERAGE_PCT * sell_val)
    exch, sebi = EXCH_TXN * turnover, SEBI * turnover
    return brokerage + STT_SELL * sell_val + exch + sebi + STAMP_BUY * buy_val + GST * (brokerage + exch + sebi)
