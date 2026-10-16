from .base import Strategy
from .ensemble import Ensemble
from .mean_reversion import MeanReversion
from .momentum import Momentum
from .trend import TrendFollowing

ALL = {
    "trend": TrendFollowing,
    "momentum": Momentum,
    "mean_reversion": MeanReversion,
}


def build(name: str) -> Strategy:
    name = name.lower()
    if name == "ensemble":
        return Ensemble([cls() for cls in ALL.values()])
    if name not in ALL:
        raise ValueError(f"Unknown strategy '{name}'. Choose one of {sorted(ALL)} or 'ensemble'.")
    return ALL[name]()


__all__ = ["Strategy", "Ensemble", "MeanReversion", "Momentum", "TrendFollowing", "ALL", "build"]
