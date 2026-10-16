"""Mean reversion: buy sharp dips in an otherwise healthy market."""
from __future__ import annotations

from .. import indicators as ind
from ..models import Candle, Position, Side, Signal
from .base import Strategy, clamp, closes


class MeanReversion(Strategy):
    name = "mean_reversion"
    warmup = 220

    def __init__(self, window: int = 50, z_entry: float = -2.0, z_exit: float = 0.0,
                 rsi_window: int = 14, rsi_entry: float = 32.0, rsi_exit: float = 55.0,
                 regime_window: int = 200, crash_lookback: int = 288, crash_threshold: float = -0.08):
        self.window = window
        self.z_entry = z_entry
        self.z_exit = z_exit
        self.rsi_window = rsi_window
        self.rsi_entry = rsi_entry
        self.rsi_exit = rsi_exit
        self.regime_window = regime_window
        self.crash_lookback = crash_lookback
        self.crash_threshold = crash_threshold
        self.warmup = max(regime_window, crash_lookback, window) + 10

    def evaluate(self, product: str, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return self.hold(product, "warmup")
        c = closes(candles)
        if any(x <= 0 for x in c[-self.warmup:]):
            return self.hold(product, "bad data")
        z = ind.zscore(c, self.window)
        r = ind.rsi(c, self.rsi_window)
        regime = ind.ema(c, self.regime_window)
        day_move = ind.pct_change(c, self.crash_lookback)
        mean = ind.sma(c, self.window)
        if z is None or r is None or regime is None or day_move is None or mean is None:
            return self.hold(product, "indicators")
        price = c[-1]

        if position is not None:
            if z >= self.z_exit or r >= self.rsi_exit:
                return Signal(product, Side.SELL, self.name, 0.7, 0.0, f"reverted (z {z:+.2f}, rsi {r:.0f})")
            return self.hold(product, "waiting for reversion")

        # Only fade dips when the market is not in free fall.
        healthy = day_move > self.crash_threshold and price > regime * 0.97
        if z <= self.z_entry and r <= self.rsi_entry and healthy:
            # Expect half of the distance back to the mean.
            expected = clamp((mean - price) / price * 0.5, 0.0, 0.04)
            strength = clamp((abs(z) - abs(self.z_entry)) / 1.5, 0.0, 1.0)
            return Signal(product, Side.BUY, self.name, round(0.4 + 0.6 * strength, 3), round(expected, 5),
                          f"z {z:+.2f}, rsi {r:.0f}, 24h {day_move:+.2%}")
        return self.hold(product, "no dip or market unhealthy")
