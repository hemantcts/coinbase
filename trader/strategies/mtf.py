"""Multi-timeframe trend: higher timeframes pick the direction, lower timeframes time the entry.

Trades on 15-minute candles (GRANULARITY=900) and reads four more timeframes from the engine:

  direction  1 day   close above its 50-day EMA
             6 h     EMA 20 above EMA 100 and close above EMA 20 (the trendhold condition)
  momentum   1 h     close above EMA 20, RSI 45..75 (rising, not overbought)
             30 min  EMA 9 above EMA 21
  trigger    15 min  close crosses back above EMA 20 (a dip inside the trend is over)

A BUY needs all five. Exits are trendhold's: sell when the 6 h EMA 20 drops below EMA 100, plus the risk
controller's trailing stop of atr_multiple x the 6-hour ATR (stop_granularity), so 15-minute noise does
not shake a position out. The lower timeframes only decide *when* to enter, never how much is at risk.
"""
from __future__ import annotations

from .. import indicators as ind
from ..models import Candle, Position, Side, Signal
from .base import Strategy, clamp, closes, highs, lows

M15, M30, H1, H6, D1 = 900, 1800, 3600, 21600, 86400


class MultiTimeframe(Strategy):
    name = "mtf"
    stop_mode = "atr"
    timeframes = (M30, H1, H6, D1)
    frame_history = 150
    stop_granularity = H6

    def __init__(self, fast: int = 20, slow: int = 100, daily_ema: int = 50, atr_window: int = 14,
                 expected_atr_multiple: float = 3.0):
        self.fast = fast
        self.slow = slow
        self.daily_ema = daily_ema
        self.atr_window = atr_window
        self.expected_atr_multiple = expected_atr_multiple
        self.warmup = fast + 5
        self._cache: dict[tuple[str, int, int], dict[str, float] | None] = {}

    # Higher-timeframe indicators change once per candle of that timeframe; cache them by its last candle.
    def _frame(self, product: str, g: int, rows: list[Candle]) -> dict[str, float] | None:
        if not rows:
            return None
        key = (product, g, rows[-1].ts)
        if key not in self._cache:
            self._cache = {k: v for k, v in self._cache.items() if (k[0], k[1]) != (product, g)}
            self._cache[key] = self._indicators(g, rows)
        return self._cache[key]

    def _indicators(self, g: int, rows: list[Candle]) -> dict[str, float] | None:
        c = closes(rows)
        if g == D1:
            e = ind.ema(c, self.daily_ema)
            return None if e is None else {"close": c[-1], "ema": e}
        if g == H6:
            fast, slow = ind.ema(c, self.fast), ind.ema(c, self.slow)
            atr = ind.atr(highs(rows), lows(rows), c, self.atr_window)
            return None if fast is None or slow is None or atr is None else {"close": c[-1], "fast": fast, "slow": slow, "atr": atr}
        if g == H1:
            e, r = ind.ema(c, self.fast), ind.rsi(c, 14)
            return None if e is None or r is None else {"close": c[-1], "ema": e, "rsi": r}
        if g == M30:
            f, s = ind.ema(c, 9), ind.ema(c, 21)
            return None if f is None or s is None else {"fast": f, "slow": s}
        return None

    def evaluate(self, product: str, candles: list[Candle], position: Position | None,
                 frames: dict[int, list[Candle]] | None = None) -> Signal:
        if len(candles) < self.warmup:
            return self.hold(product, "warmup")
        frames = frames or {}
        d1, h6, h1, m30 = (self._frame(product, g, frames.get(g, [])) for g in (D1, H6, H1, M30))
        if h6 is None:
            return self.hold(product, "timeframes: not enough 6h data")

        if position is not None:
            if h6["fast"] < h6["slow"]:
                return Signal(product, Side.SELL, self.name, 0.8, 0.0,
                              f"6h ema{self.fast} crossed below ema{self.slow}", h6["atr"])
            return self.hold(product, "trend intact (6h)")

        if d1 is None or h1 is None or m30 is None:
            return self.hold(product, "timeframes: not enough 1d/1h/30m data")
        checks = [
            ("1d", d1["close"] > d1["ema"]),
            ("6h", h6["fast"] > h6["slow"] and h6["close"] > h6["fast"]),
            ("1h", h1["close"] > h1["ema"] and 45 <= h1["rsi"] <= 75),
            ("30m", m30["fast"] > m30["slow"]),
        ]
        failed = [name for name, ok in checks if not ok]
        if "1d" in failed or "6h" in failed:
            return self.hold(product, f"no uptrend ({', '.join(f for f in failed if f in ('1d', '6h'))} against)")
        if failed:
            return self.hold(product, f"uptrend, waiting for {' and '.join(failed)} momentum")

        c = closes(candles)
        e = ind.ema_series(c, self.fast)
        if e[-1] is None or e[-2] is None:
            return self.hold(product, "indicators")
        if not (c[-2] <= e[-2] and c[-1] > e[-1]):
            return self.hold(product, "all timeframes agree, waiting for the 15m trigger")
        price = c[-1]
        expected = clamp(self.expected_atr_multiple * h6["atr"] / price, 0.0, 0.08)
        strength = clamp((h6["fast"] / h6["slow"] - 1) / 0.03, 0.0, 1.0)
        return Signal(product, Side.BUY, self.name, round(0.5 + 0.5 * strength, 3), round(expected, 5),
                      f"1d+6h uptrend, 1h+30m momentum, 15m close {price:.2f} back above ema{self.fast}", h6["atr"])
