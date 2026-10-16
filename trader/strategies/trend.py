"""Trend following: buy when the uptrend is confirmed by price and volume."""
from __future__ import annotations

from .. import indicators as ind
from ..models import Candle, Position, Side, Signal
from .base import Strategy, clamp, closes, volumes


class TrendFollowing(Strategy):
    name = "trend"
    warmup = 120

    def __init__(self, fast: int = 20, slow: int = 50, trend_filter: int = 100,
                 volume_window: int = 30, volume_factor: float = 1.1, horizon: int = 24):
        self.fast = fast
        self.slow = slow
        self.trend_filter = trend_filter
        self.volume_window = volume_window
        self.volume_factor = volume_factor
        self.horizon = horizon
        self.warmup = max(trend_filter, slow, volume_window) + 5

    def evaluate(self, product: str, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return self.hold(product, "warmup")
        c = closes(candles)
        if any(x <= 0 for x in c[-self.warmup:]):
            return self.hold(product, "bad data")
        fast = ind.ema(c, self.fast)
        slow = ind.ema(c, self.slow)
        filt = ind.ema(c, self.trend_filter)
        v = volumes(candles)
        avg_vol = ind.sma(v[:-1], self.volume_window)
        slope = ind.slope(c, self.slow) or 0.0
        if fast is None or slow is None or filt is None or avg_vol is None:
            return self.hold(product, "indicators")
        price = c[-1]

        if position is not None:
            # Exit when the short trend rolls over or price loses the slow average.
            if fast < slow or price < slow * 0.995:
                return Signal(product, Side.SELL, self.name, 0.8, 0.0, "trend rolled over")
            return self.hold(product, "riding trend")

        uptrend = fast > slow * 1.001 and slow > filt and price > fast
        vol_ok = v[-1] >= avg_vol * self.volume_factor
        if uptrend and vol_ok and slope > 0:
            # Expected move: continue the recent slope over the horizon, capped.
            expected = clamp(slope * self.horizon, 0.0, 0.05)
            strength = clamp((fast / slow - 1) / 0.01, 0.0, 1.0)
            return Signal(product, Side.BUY, self.name, round(0.5 + 0.5 * strength, 3), round(expected, 5),
                          f"ema{self.fast}>ema{self.slow}>ema{self.trend_filter}, volume {v[-1]/avg_vol:.2f}x")
        return self.hold(product, "no confirmed uptrend")


