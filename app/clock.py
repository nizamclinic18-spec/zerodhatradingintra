from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    """Railway/VPS servers run in UTC. Every trading-time decision uses IST."""
    return datetime.now(IST)
