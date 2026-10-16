"""Momentum: ride short-term moves while they clearly outrun the costs."""
from __future__ import annotations

from .. import indicators as ind
from ..models import Candle, Position, Side, Signal
from .base import Strategy, clamp, closes


class Momentum(Strategy):
    name = "momentum"
    warmup = 80

    def __init__(self, lookback: int = 12, confirm: int = 36, entry_threshold: float = 0.006,
                 rsi_window: int = 14, rsi_max: float = 70.0, rsi_exit: float = 80.0):
        self.lookback = lookback
        self.confirm = confirm
        self.entry_threshold = entry_threshold
        self.rsi_window = rsi_window
        self.rsi_max = rsi_max
        self.rsi_exit = rsi_exit
        self.warmup = max(confirm, rsi_window) + 10

    def evaluate(self, product: str, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return self.hold(product, "warmup")
        c = closes(candles)
        if any(x <= 0 for x in c[-self.warmup:]):
            return self.hold(product, "bad data")
        short = ind.pct_change(c, self.lookback)
        longer = ind.pct_change(c, self.confirm)
        r = ind.rsi(c, self.rsi_window)
        vol = ind.realised_vol(c, self.confirm) or 0.0
        if short is None or longer is None or r is None:
            return self.hold(product, "indicators")

        if position is not None:
            if short < -0.002 or r > self.rsi_exit:
                return Signal(product, Side.SELL, self.name, 0.7, 0.0, f"momentum faded ({short:+.3%}, rsi {r:.0f})")
            return self.hold(product, "momentum intact")

        if short > self.entry_threshold and longer > 0 and r < self.rsi_max:
            # Expect roughly half of the recent move to continue; scale down in chaotic markets.
            expected = clamp(short * 0.5, 0.0, 0.04)
            if vol > 0 and short / (vol * (self.lookback ** 0.5)) < 1.0:
                return self.hold(product, "move within noise")
            strength = clamp(short / (self.entry_threshold * 3), 0.0, 1.0)
            return Signal(product, Side.BUY, self.name, round(0.4 + 0.6 * strength, 3), round(expected, 5),
                          f"{self.lookback}-bar {short:+.3%}, {self.confirm}-bar {longer:+.3%}, rsi {r:.0f}")
        return self.hold(product, "no momentum")
