"""Technical indicators on plain Python sequences. No lookahead anywhere."""
from __future__ import annotations

import math
from typing import Sequence


def sma(values: Sequence[float], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    return sum(values[-window:]) / window


def sma_series(values: Sequence[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if window <= 0 or len(values) < window:
        return out
    total = sum(values[:window])
    out[window - 1] = total / window
    for i in range(window, len(values)):
        total += values[i] - values[i - window]
        out[i] = total / window
    return out


def ema_series(values: Sequence[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if window <= 0 or len(values) < window:
        return out
    k = 2 / (window + 1)
    e = sum(values[:window]) / window
    out[window - 1] = e
    for i in range(window, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def ema(values: Sequence[float], window: int) -> float | None:
    s = ema_series(values, window)
    return s[-1] if s else None


def rsi(values: Sequence[float], window: int = 14) -> float | None:
    """Wilder's RSI."""
    if len(values) < window + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, window + 1):
        d = values[i] - values[i - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    avg_gain = gains / window
    avg_loss = losses / window
    for i in range(window + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_gain = (avg_gain * (window - 1) + max(d, 0.0)) / window
        avg_loss = (avg_loss * (window - 1) + max(-d, 0.0)) / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], window: int = 14) -> float | None:
    """Average true range (Wilder smoothing)."""
    n = len(closes)
    if n < window + 1:
        return None
    trs = []
    for i in range(1, n):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    a = sum(trs[:window]) / window
    for tr in trs[window:]:
        a = (a * (window - 1) + tr) / window
    return a


def stdev(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    m = sum(values) / n
    return math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))


def zscore(values: Sequence[float], window: int) -> float | None:
    if len(values) < window:
        return None
    w = values[-window:]
    sd = stdev(w)
    if sd == 0:
        return 0.0
    return (values[-1] - sum(w) / window) / sd


def returns(values: Sequence[float]) -> list[float]:
    return [values[i] / values[i - 1] - 1.0 for i in range(1, len(values)) if values[i - 1] > 0]


def realised_vol(values: Sequence[float], window: int) -> float | None:
    """Standard deviation of simple returns over the window (per candle)."""
    if len(values) < window + 1:
        return None
    return stdev(returns(values[-(window + 1):]))


def pct_change(values: Sequence[float], lookback: int) -> float | None:
    if len(values) <= lookback or values[-1 - lookback] <= 0:
        return None
    return values[-1] / values[-1 - lookback] - 1.0


def slope(values: Sequence[float], window: int) -> float | None:
    """Least-squares slope of the last `window` values, as a fraction of the mean per step."""
    if len(values) < window or window < 2:
        return None
    w = values[-window:]
    xs = range(window)
    mx = (window - 1) / 2
    my = sum(w) / window
    num = sum((x - mx) * (y - my) for x, y in zip(xs, w))
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0 or my == 0:
        return 0.0
    return (num / den) / my
