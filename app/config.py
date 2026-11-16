import os
from dataclasses import dataclass
from datetime import time


def _t(name: str, default: str) -> time:
    h, m = os.getenv(name, default).split(":")
    return time(int(h), int(m))


@dataclass(frozen=True)
class Config:
    api_key: str
    api_secret: str
    admin_password: str
    mode: str
    data_dir: str
    capital: float
    max_daily_loss_pct: float
    risk_per_trade_pct: float
    max_open_positions: int
    max_notional_mult: float
    watchlist: tuple
    candle_minutes: int
    or_start: time
    or_end: time
    no_new_entry: time
    square_off: time
    max_stop_pct: float
    target_r: float
    poll_seconds: float = 3.0

    @property
    def loss_limit(self) -> float:
        """Rupee amount. Day P&L at or below -loss_limit triggers the kill-switch."""
        return self.capital * self.max_daily_loss_pct / 100.0


def load() -> Config:
    mode = os.getenv("TRADING_MODE", "paper").strip().lower()
    if mode not in ("paper", "live"):
        raise ValueError("TRADING_MODE must be 'paper' or 'live'")
    return Config(
        api_key=os.getenv("KITE_API_KEY", ""),
        api_secret=os.getenv("KITE_API_SECRET", ""),
        admin_password=os.getenv("DASHBOARD_PASSWORD", ""),
        mode=mode,
        data_dir=os.getenv("DATA_DIR", "./data"),
        capital=float(os.getenv("CAPITAL", "100000")),
        max_daily_loss_pct=float(os.getenv("MAX_DAILY_LOSS_PCT", "1.5")),
        risk_per_trade_pct=float(os.getenv("RISK_PER_TRADE_PCT", "0.5")),
        max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "2")),
        max_notional_mult=float(os.getenv("MAX_NOTIONAL_MULT", "1.0")),
        watchlist=tuple(s.strip().upper() for s in os.getenv("WATCHLIST", "RELIANCE,TCS,INFY").split(",") if s.strip()),
        candle_minutes=int(os.getenv("CANDLE_MINUTES", "5")),
        or_start=_t("OR_START", "09:15"),
        or_end=_t("OR_END", "09:30"),
        no_new_entry=_t("NO_NEW_ENTRY", "14:30"),
        square_off=_t("SQUARE_OFF", "15:10"),
        max_stop_pct=float(os.getenv("MAX_STOP_PCT", "1.0")),
        target_r=float(os.getenv("TARGET_R", "2.0")),
    )
