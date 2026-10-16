import math

from trader import indicators as ind


def test_sma_and_series():
    v = [1, 2, 3, 4, 5]
    assert ind.sma(v, 3) == 4
    assert ind.sma(v, 6) is None
    assert ind.sma_series(v, 3) == [None, None, 2, 3, 4]


def test_ema_converges_to_constant():
    v = [10.0] * 50
    assert math.isclose(ind.ema(v, 10), 10.0)


def test_rsi_bounds_and_direction():
    up = [float(i) for i in range(1, 40)]
    down = [float(40 - i) for i in range(1, 40)]
    assert ind.rsi(up) == 100.0
    assert ind.rsi(down) == 0.0
    assert ind.rsi([1.0] * 5) is None


def test_zscore_and_stdev():
    v = [1, 2, 3, 4, 5, 6, 7, 8, 9, 20]
    z = ind.zscore(v, 10)
    assert z > 2
    assert ind.stdev([5, 5, 5]) == 0
    assert ind.zscore([5] * 10, 10) == 0.0


def test_atr_positive_and_pct_change():
    highs = [11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26]
    lows = [h - 2 for h in highs]
    closes = [h - 1 for h in highs]
    assert ind.atr(highs, lows, closes, 14) > 0
    assert math.isclose(ind.pct_change([100, 110], 1), 0.1)
    assert ind.pct_change([100], 1) is None


def test_slope_sign():
    assert ind.slope([1, 2, 3, 4, 5], 5) > 0
    assert ind.slope([5, 4, 3, 2, 1], 5) < 0
