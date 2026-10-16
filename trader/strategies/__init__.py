from .base import Strategy
from .ensemble import Ensemble
from .mean_reversion import MeanReversion
from .momentum import Momentum
from .trend import TrendFollowing
from .trendhold import TrendHold

ALL = {
    "trendhold": TrendHold,
    "trend": TrendFollowing,
    "momentum": Momentum,
    "mean_reversion": MeanReversion,
}
# The ensemble combines the three fast strategies; trendhold runs on its own timeframe.
ENSEMBLE_MEMBERS = ("trend", "momentum", "mean_reversion")


def build(name: str) -> Strategy:
    name = name.lower()
    if name == "ensemble":
        return Ensemble([ALL[m]() for m in ENSEMBLE_MEMBERS])
    if name not in ALL:
        raise ValueError(f"Unknown strategy '{name}'. Choose one of {sorted(ALL)} or 'ensemble'.")
    return ALL[name]()


__all__ = ["Strategy", "Ensemble", "MeanReversion", "Momentum", "TrendFollowing", "TrendHold", "ALL", "build"]
