"""Strategy interface.

A strategy only ever sees candles and its own open position. It returns a
Signal with an *estimate* of the favourable move. It has no idea about the
account size, the limits or the costs: that is the risk controller's job.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Candle, Position, Signal


class Strategy(ABC):
    name: str = "base"
    warmup: int = 50          # candles needed before the strategy may speak

    @abstractmethod
    def evaluate(self, product: str, candles: list[Candle], position: Position | None) -> Signal:
        """Return BUY to open, SELL to close, HOLD to do nothing."""

    def hold(self, product: str, reason: str = "") -> Signal:
        return Signal.hold(product, self.name, reason)


def closes(candles: list[Candle]) -> list[float]:
    return [c.close for c in candles]


def highs(candles: list[Candle]) -> list[float]:
    return [c.high for c in candles]


def lows(candles: list[Candle]) -> list[float]:
    return [c.low for c in candles]


def volumes(candles: list[Candle]) -> list[float]:
    return [c.volume for c in candles]


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
