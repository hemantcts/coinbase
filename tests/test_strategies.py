from conftest import make_candles

from trader.models import Position, Side
from trader.strategies import build
from trader.strategies.mean_reversion import MeanReversion
from trader.strategies.momentum import Momentum
from trader.strategies.trend import TrendFollowing


def test_all_strategies_hold_during_warmup():
    rows = make_candles(20)
    for name in ("trend", "momentum", "mean_reversion", "ensemble"):
        s = build(name).evaluate("BTC-EUR", rows, None)
        assert s.side == Side.HOLD and s.product == "BTC-EUR"


def test_trend_buys_in_confirmed_uptrend_with_volume():
    rows = make_candles(400, drift=0.001, vol=0.0005, seed=3)
    last = rows[-1]
    rows[-1] = type(last)(last.ts, last.open, last.high, last.low, last.close, 50.0)   # volume spike
    sig = TrendFollowing().evaluate("BTC-EUR", rows, None)
    assert sig.side == Side.BUY
    assert 0 < sig.expected_move <= 0.05
    assert 0.5 <= sig.confidence <= 1.0


def test_trend_exits_when_trend_rolls_over():
    rows = make_candles(300, drift=0.001, vol=0.0005, seed=4) + make_candles(80, start_price=130, drift=-0.004, vol=0.0005, seed=5, t0=1_900_000_000)
    pos = Position("BTC-EUR", 1.0, 120.0, "x", "trend", 100.0, 200.0)
    assert TrendFollowing().evaluate("BTC-EUR", rows, pos).side == Side.SELL


def test_momentum_buys_on_strong_move_and_exits_when_it_fades():
    flat = make_candles(80, drift=0.0, vol=0.0003, seed=6)
    burst = make_candles(14, start_price=flat[-1].close, drift=0.0015, vol=0.0002, seed=7, t0=flat[-1].ts + 300)
    rows = flat + burst
    # 14 straight green candles: RSI ~96, the default overbought filter keeps us out.
    assert Momentum().evaluate("ETH-EUR", rows, None).side == Side.HOLD
    sig = Momentum(rsi_max=99).evaluate("ETH-EUR", rows, None)
    assert sig.side == Side.BUY and sig.expected_move > 0
    fade = make_candles(14, start_price=rows[-1].close, drift=-0.001, vol=0.0002, seed=8, t0=rows[-1].ts + 300)
    pos = Position("ETH-EUR", 1.0, rows[-1].close, "x", "momentum", 0, 1e9)
    assert Momentum().evaluate("ETH-EUR", rows + fade, pos).side == Side.SELL


def test_mean_reversion_buys_sharp_dip_in_healthy_market_only():
    base = make_candles(300, drift=0.0, vol=0.0005, seed=9)
    dip = make_candles(6, start_price=base[-1].close, drift=-0.004, vol=0.0002, seed=10, t0=base[-1].ts + 300)
    sig = MeanReversion().evaluate("BTC-EUR", base + dip, None)
    assert sig.side == Side.BUY and sig.expected_move > 0
    # Same dip, but the market has crashed over the day: stay out.
    crash = make_candles(300, drift=-0.0004, vol=0.0005, seed=11)
    dip2 = make_candles(6, start_price=crash[-1].close, drift=-0.004, vol=0.0002, seed=12, t0=crash[-1].ts + 300)
    assert MeanReversion().evaluate("BTC-EUR", crash + dip2, None).side == Side.HOLD


def test_ensemble_picks_best_buy_and_delegates_exit_to_owner():
    rows = make_candles(400, drift=0.001, vol=0.0005, seed=3)
    last = rows[-1]
    rows[-1] = type(last)(last.ts, last.open, last.high, last.low, last.close, 50.0)
    ens = build("ensemble")
    sig = ens.evaluate("BTC-EUR", rows, None)
    assert sig.side == Side.BUY and sig.strategy in ("trend", "momentum", "mean_reversion")
    pos = Position("BTC-EUR", 1.0, 50.0, "x", "momentum", 0, 1e9)
    out = ens.evaluate("BTC-EUR", rows, pos)
    assert out.strategy == "momentum"
