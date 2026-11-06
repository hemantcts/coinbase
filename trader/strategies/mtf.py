"""Multi-timeframe trend: higher timeframes pick the direction, lower timeframes time the entry.

Trades on 15-minute candles (GRANULARITY=900) and reads six more timeframes from the engine:

  direction  1 day   close above its 50-day EMA
             4 h     EMA 20 above EMA 100 and close above EMA 20 (the trendhold condition)
  momentum   2 h     close above EMA 20
             1 h     close above EMA 20, RSI 45..75 (rising, not overbought)
             30 min  EMA 9 above EMA 21
  trigger    15 min  close crosses back above EMA 20 (a dip inside the trend is over)
  execution  1 min   no sharp drop in the last 5 minutes (high-to-close under 1 %) and price still
                     within 0.5 % of the 15-minute close; skipped when no 1-minute data exists (backtests)

A BUY needs all of them. Exits: sell when the 4 h EMA 20 drops below EMA 100, plus the risk controller's
trailing stop of atr_multiple x the 4-hour ATR (stop_granularity), so 15-minute noise does not shake a
position out. The lower timeframes only decide *when* to enter, never how much is at risk.
Every HOLD reason lists each timeframe as ok (+) or against (-), so the dashboard shows what is missing.
"""
from __future__ import annotations

from .. import indicators as ind
from ..models import Candle, Position, Side, Signal
from .base import Strategy, clamp, closes, highs, lows

M1, M15, M30, H1, H2, H4, D1 = 60, 900, 1800, 3600, 7200, 14400, 86400
NAMES = {M1: "1m", M15: "15m", M30: "30m", H1: "1h", H2: "2h", H4: "4h", D1: "1d"}


class MultiTimeframe(Strategy):
    name = "mtf"
    stop_mode = "atr"
    timeframes = (M1, M30, H1, H2, H4, D1)
    frame_history = 150
    stop_granularity = H4

    def __init__(self, fast: int = 20, slow: int = 100, daily_ema: int = 50, atr_window: int = 14,
                 expected_atr_multiple: float = 3.0, max_1m_drop: float = 0.01, max_1m_gap: float = 0.005):
        self.fast = fast
        self.slow = slow
        self.daily_ema = daily_ema
        self.atr_window = atr_window
        self.expected_atr_multiple = expected_atr_multiple
        self.max_1m_drop = max_1m_drop
        self.max_1m_gap = max_1m_gap
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
        if g == H4:
            fast, slow = ind.ema(c, self.fast), ind.ema(c, self.slow)
            atr = ind.atr(highs(rows), lows(rows), c, self.atr_window)
            return None if fast is None or slow is None or atr is None else {"close": c[-1], "fast": fast, "slow": slow, "atr": atr}
        if g == H2:
            e = ind.ema(c, self.fast)
            return None if e is None else {"close": c[-1], "ema": e}
        if g == H1:
            e, r = ind.ema(c, self.fast), ind.rsi(c, 14)
            return None if e is None or r is None else {"close": c[-1], "ema": e, "rsi": r}
        if g == M30:
            f, s = ind.ema(c, 9), ind.ema(c, 21)
            return None if f is None or s is None else {"fast": f, "slow": s}
        return None

    def _one_minute_ok(self, rows: list[Candle], ref: float) -> bool | None:
        """None = no 1-minute data (the check is skipped); else True when the last minutes look calm."""
        if len(rows) < 5:
            return None
        last = rows[-5:]
        drop = 1 - last[-1].close / max(c.high for c in last)
        return drop < self.max_1m_drop and last[-1].close >= ref * (1 - self.max_1m_gap)

    def evaluate(self, product: str, candles: list[Candle], position: Position | None,
                 frames: dict[int, list[Candle]] | None = None) -> Signal:
        if len(candles) < self.warmup:
            return self.hold(product, "warmup")
        frames = frames or {}
        d1, h4, h2, h1, m30 = (self._frame(product, g, frames.get(g, [])) for g in (D1, H4, H2, H1, M30))
        if h4 is None:
            return self.hold(product, "timeframes: not enough 4h data")

        if position is not None:
            if h4["fast"] < h4["slow"]:
                return Signal(product, Side.SELL, self.name, 0.8, 0.0,
                              f"4h ema{self.fast} crossed below ema{self.slow}", h4["atr"])
            return self.hold(product, "trend intact (4h)")

        missing = [NAMES[g] for g, f in ((D1, d1), (H2, h2), (H1, h1), (M30, m30)) if f is None]
        if missing:
            return self.hold(product, f"timeframes: not enough {'/'.join(missing)} data")
        c = closes(candles)
        e = ind.ema_series(c, self.fast)
        if e[-1] is None or e[-2] is None:
            return self.hold(product, "indicators")
        one_min = self._one_minute_ok(frames.get(M1, []), c[-1])
        checks = [
            ("1d", d1["close"] > d1["ema"]),
            ("4h", h4["fast"] > h4["slow"] and h4["close"] > h4["fast"]),
            ("2h", h2["close"] > h2["ema"]),
            ("1h", h1["close"] > h1["ema"] and 45 <= h1["rsi"] <= 75),
            ("30m", m30["fast"] > m30["slow"]),
            ("15m", c[-2] <= e[-2] and c[-1] > e[-1]),
            ("1m", one_min is not False),
        ]
        board = " ".join(f"{n}{'+' if ok else '-'}" for n, ok in checks) + ("" if one_min is not None else " (1m n/a)")
        failed = [n for n, ok in checks if not ok]
        if "1d" in failed or "4h" in failed:
            return self.hold(product, f"no uptrend · {board}")
        if any(n in failed for n in ("2h", "1h", "30m")):
            return self.hold(product, f"uptrend, waiting for momentum · {board}")
        if "15m" in failed:
            return self.hold(product, f"waiting for the 15m trigger · {board}")
        if "1m" in failed:
            return self.hold(product, f"1m price unsettled, entry postponed · {board}")
        price = c[-1]
        expected = clamp(self.expected_atr_multiple * h4["atr"] / price, 0.0, 0.08)
        strength = clamp((h4["fast"] / h4["slow"] - 1) / 0.03, 0.0, 1.0)
        return Signal(product, Side.BUY, self.name, round(0.5 + 0.5 * strength, 3), round(expected, 5),
                      f"all timeframes agree · {board}", h4["atr"])
