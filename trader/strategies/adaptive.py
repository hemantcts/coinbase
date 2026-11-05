"""Adaptive: classify each coin's market regime on every closed candle and pick the matching play.

  uptrend    fast EMA above slow EMA, slow EMA rising
               - price above the fast EMA  -> trend entry (as trendhold), trailing ATR stop
               - pullback to between EMAs with RSI < 45 -> swing entry, trailing ATR stop, exit on RSI > 70
  range      slow EMA flat (within 1 % over 5 days) and price chopping sideways (low efficiency ratio)
               - oversold dip (RSI < 35, z-score < -1.5) -> DCA ladder: first rung now, further rungs on
                 deeper dips; take-profit above the average entry, hard stop below the first entry
  downtrend  fast EMA below slow EMA, slow EMA down more than 1 % over 5 days -> no new entries; DCA ladders closed
  mixed      anything else -> no new entries

Designed for 6-hour candles like trendhold. Scalping is deliberately absent: on Coinbase's 0.6 % / 1.2 %
fees the fast strategies had no edge (docs/RESEARCH-2026-10.md). The strategy only proposes; ladder
size, rung spacing, stop and take-profit are set and enforced by the risk profile (max_dca_levels etc.).
"""
from __future__ import annotations

from .. import indicators as ind
from ..models import Candle, Position, Side, Signal
from .base import Strategy, clamp, closes, highs, lows

TREND, SWING, DCA = "adaptive:trend", "adaptive:swing", "adaptive:dca"


def efficiency_ratio(values: list[float], window: int) -> float | None:
    """Net move over the window divided by the path length: 1 = straight line, near 0 = chop."""
    if len(values) <= window:
        return None
    w = values[-(window + 1):]
    path = sum(abs(w[i] - w[i - 1]) for i in range(1, len(w)))
    return abs(w[-1] - w[0]) / path if path > 0 else 0.0


class Adaptive(Strategy):
    name = "adaptive"
    stop_mode = "atr"

    def __init__(self, fast: int = 20, slow: int = 100, atr_window: int = 14, rsi_window: int = 14,
                 slope_lookback: int = 20, er_window: int = 60, flat_band: float = 0.01, range_er: float = 0.3,
                 expected_atr_multiple: float = 3.0):
        self.fast = fast
        self.slow = slow
        self.atr_window = atr_window
        self.rsi_window = rsi_window
        self.slope_lookback = slope_lookback
        self.er_window = er_window
        self.flat_band = flat_band
        self.range_er = range_er
        self.expected_atr_multiple = expected_atr_multiple
        self.warmup = slow + slope_lookback + 5

    def regime(self, c: list[float]) -> tuple[str, dict[str, float]]:
        fast_s = ind.ema_series(c, self.fast)
        slow_s = ind.ema_series(c, self.slow)
        fast, slow, slow_then = fast_s[-1], slow_s[-1], slow_s[-1 - self.slope_lookback]
        er = efficiency_ratio(c, self.er_window)
        if fast is None or slow is None or slow_then is None or er is None:
            return "unknown", {}
        # The regime is read from the slow EMA's drift and a long efficiency window, so a few-candle dip
        # inside a sideways market (exactly what DCA wants to buy) does not flip it to "downtrend".
        spread = fast / slow - 1
        drift = slow / slow_then - 1
        m = {"fast": fast, "slow": slow, "spread": spread, "drift": drift, "er": er}
        if abs(drift) <= self.flat_band and er < self.range_er:
            return "range", m
        if spread > 0 and drift > 0:
            return "uptrend", m
        if spread < 0 and drift < -self.flat_band:
            return "downtrend", m
        return "mixed", m

    def evaluate(self, product: str, candles: list[Candle], position: Position | None) -> Signal:
        if len(candles) < self.warmup:
            return self.hold(product, "warmup")
        c = closes(candles)
        if any(x <= 0 for x in c[-self.warmup:]):
            return self.hold(product, "bad data")
        regime, m = self.regime(c)
        atr = ind.atr(highs(candles), lows(candles), c, self.atr_window)
        rsi = ind.rsi(c, self.rsi_window)
        z = ind.zscore(c, self.fast)
        if not m or atr is None or rsi is None or z is None:
            return self.hold(product, "indicators")
        price, fast, slow = c[-1], m["fast"], m["slow"]
        tag = f"{regime}, ema{self.fast}/{self.slow} {m['spread']:+.2%}, er {m['er']:.2f}, rsi {rsi:.0f}"

        if position is not None:
            return self._exit_or_add(product, position, regime, price, fast, slow, rsi, z, c, atr, tag)

        if regime == "uptrend" and price > fast:
            expected = clamp(self.expected_atr_multiple * atr / price, 0.0, 0.08)
            strength = clamp(m["spread"] / 0.03, 0.0, 1.0)
            return Signal(product, Side.BUY, TREND, round(0.5 + 0.5 * strength, 3), round(expected, 5),
                          f"trend entry ({tag})", atr, stop_mode="atr")
        if regime == "uptrend" and slow < price <= fast and rsi < 45:
            recent_high = max(highs(candles)[-self.fast:])
            expected = clamp(recent_high / price - 1, 0.0, 0.08)
            return Signal(product, Side.BUY, SWING, 0.6, round(expected, 5), f"swing pullback ({tag})", atr, stop_mode="atr")
        if regime == "range" and rsi < 35 and z < -1.5:
            mean = sum(c[-self.fast:]) / self.fast
            expected = clamp(mean / price - 1, 0.0, 0.08)
            return Signal(product, Side.BUY, DCA, 0.5, round(expected, 5), f"DCA first rung ({tag})", atr, stop_mode="dca")
        return self.hold(product, f"no entry: {tag}")

    def _exit_or_add(self, product: str, pos: Position, regime: str, price: float, fast: float, slow: float,
                     rsi: float, z: float, c: list[float], atr: float, tag: str) -> Signal:
        owner = pos.strategy if pos.strategy in (TREND, SWING, DCA) else TREND   # e.g. a trendhold position after a switch
        if owner == DCA:
            if regime == "downtrend":
                return Signal(product, Side.SELL, DCA, 0.9, 0.0, f"DCA ladder closed, market turned down ({tag})", atr)
            if rsi < 35:
                # The risk controller decides whether this dip is deep enough for the next rung.
                mean = sum(c[-self.fast:]) / self.fast
                expected = clamp(max(mean / price - 1, pos.entry_price / price - 1), 0.0, 0.12)
                return Signal(product, Side.BUY, DCA, 0.5, round(expected, 5), f"DCA add ({tag})", atr,
                              stop_mode="dca", add=True)
            return self.hold(product, f"DCA ladder open, waiting for take-profit ({tag})")
        if fast < slow:
            return Signal(product, Side.SELL, owner, 0.8, 0.0, f"ema{self.fast} crossed below ema{self.slow} ({tag})", atr)
        if owner == SWING and rsi > 70:
            return Signal(product, Side.SELL, SWING, 0.7, 0.0, f"swing target, RSI {rsi:.0f} ({tag})", atr)
        return self.hold(product, f"trend intact ({tag})")
