from .config import Config


class Risk:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def size(self, entry: float, stop: float) -> int:
        """Shares to trade: risk budget / per-share risk, capped by max notional.
        The cap matters: a tight stop would otherwise size you into a huge position."""
        per_share = abs(entry - stop)
        if per_share <= 0 or entry <= 0:
            return 0
        by_risk = int((self.cfg.capital * self.cfg.risk_per_trade_pct / 100.0) // per_share)
        by_notional = int((self.cfg.capital * self.cfg.max_notional_mult) // entry)
        return max(min(by_risk, by_notional), 0)

    def breached(self, day_pnl: float) -> bool:
        return day_pnl <= -self.cfg.loss_limit
