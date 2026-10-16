"""Slow trend-hold: be long while the fast EMA sits above the slow EMA, otherwise stay in cash.

Designed for 6-hour candles and a small account with 0.6 % fees: one or two
trades a month, a wide ATR trailing stop handled by the risk controller, no
take-profit. EMA 20/100 on 6h candles (5 days / 25 days) was the most
consistent setting across two years of BTC-EUR / ETH-EUR history: never the
best in any half-year, never the worst, and ahead of buy-and-hold over the
full period. Faster settings bled on fees; see docs/RESEARCH-2026-10.md.
"""
from __future__ import annotations

from .. import indicators as ind
from ..models import Candle, Position, Side, Signal
from .base import Strategy, clamp, closes, highs, lows


class TrendHold(Strategy):
    name = "trendhold"
    stop_mode = "atr"

    def __init__(self, fast: int = 20, slow: int = 100, atr_window: int = 14, expected_atr_multiple: float = 3.0):
        self.fast = fast
        self.slow = slow
        self.atr_window = atr_window
        self.expected_atr_multiple = expected_atr_multiple
        self.warmup = max(slow, atr_window) + 5

    def evaluate(self, product: str, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return self.hold(product, "warmup")
        c = closes(candles)
        if any(x <= 0 for x in c[-self.warmup:]):
            return self.hold(product, "bad data")
        fast = ind.ema(c, self.fast)
        slow = ind.ema(c, self.slow)
        atr = ind.atr(highs(candles), lows(candles), c, self.atr_window)
        if fast is None or slow is None or atr is None:
            return self.hold(product, "indicators")
        price = c[-1]
        if position is not None:
            if fast < slow:
                return Signal(product, Side.SELL, self.name, 0.8, 0.0, f"ema{self.fast} crossed below ema{self.slow}", atr)
            return self.hold(product, "trend intact")
        if fast > slow and price > fast:
            # A trend that is worth paying for should run several ATRs. On fast candles
            # the ATR is tiny and this estimate correctly fails the cost filter.
            expected = clamp(self.expected_atr_multiple * atr / price, 0.0, 0.08)
            strength = clamp((fast / slow - 1) / 0.03, 0.0, 1.0)
            return Signal(product, Side.BUY, self.name, round(0.5 + 0.5 * strength, 3), round(expected, 5),
                          f"ema{self.fast} {fast:.2f} > ema{self.slow} {slow:.2f}, atr {atr / price:.2%}", atr)
        return self.hold(product, "no uptrend")
