import os

from .strategy import ORBStrategy
from .strategy_ema import EMACrossStrategy

# Add new strategies here: name -> class taking (cfg).
STRATEGIES = {"orb": ORBStrategy, "ema": EMACrossStrategy}


def make_strategy(cfg, name=None):
    name = (name or os.getenv("STRATEGY", "orb")).lower()
    if name not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{name}'. Options: {sorted(STRATEGIES)}")
    return STRATEGIES[name](cfg)
