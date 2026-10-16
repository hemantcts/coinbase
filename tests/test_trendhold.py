from datetime import datetime, timezone

import pytest

from conftest import G, make_candles
from trader.config import PROFILES, Settings
from trader.data import StoreDataSource, resample
from trader.engine import Engine
from trader.execution.paper import PaperBroker
from trader.models import Candle, OrderIntent, Position, Side, Signal
from trader.risk import RiskController
from trader.store import Store
from trader.strategies import build
from trader.strategies.trendhold import TrendHold


def test_trendhold_buys_in_uptrend_and_sells_on_cross_down():
    up = make_candles(160, drift=0.004, vol=0.004, seed=21)
    sig = TrendHold().evaluate("BTC-EUR", up, None)
    assert sig.side == Side.BUY and sig.atr > 0 and sig.expected_move > 0
    down = up + make_candles(80, start_price=up[-1].close, drift=-0.006, vol=0.004, seed=22, t0=up[-1].ts + G)
    pos = Position("BTC-EUR", 1.0, up[-1].close, "x", "trendhold", 0.0, 0.0)
    assert TrendHold().evaluate("BTC-EUR", down, pos).side == Side.SELL
    assert TrendHold().evaluate("BTC-EUR", up[:20], None).side == Side.HOLD      # warmup


def test_trendhold_expected_move_scales_with_volatility_so_fast_candles_fail_the_cost_filter():
    calm = make_candles(160, drift=0.001, vol=0.0005, seed=23)
    wild = make_candles(160, drift=0.004, vol=0.01, seed=23)
    s_calm = TrendHold().evaluate("BTC-EUR", calm, None)
    s_wild = TrendHold().evaluate("BTC-EUR", wild, None)
    assert s_calm.side == Side.BUY and s_wild.side == Side.BUY
    assert s_calm.expected_move < s_wild.expected_move
    rc = RiskController(PROFILES["medium"], Settings().costs)
    state_calm = _state(rc)
    d = rc.review(OrderIntent("BTC-EUR", Side.BUY, calm[-1].close, s_calm, stop_mode="atr"), state_calm)
    assert not d.approved and "expected move" in d.reason


def _state(rc):
    from trader.models import PortfolioState
    st = PortfolioState(cash=1000, initial_capital=1000)
    rc.roll_day(st, datetime(2026, 10, 16, tzinfo=timezone.utc))
    return st


def test_atr_stop_sizing_and_no_take_profit():
    rc = RiskController(PROFILES["medium"], Settings().costs)
    sig = Signal("BTC-EUR", Side.BUY, "trendhold", 0.8, 0.05, "", atr=2.0)
    d = rc.review(OrderIntent("BTC-EUR", Side.BUY, 100.0, sig, stop_mode="atr"), _state(rc))
    assert d.approved
    assert d.stop_price == pytest.approx(100.0 - 6.0 * 2.0)       # medium: 6 x ATR
    assert d.take_profit_price == 0.0
    d_pct = rc.review(OrderIntent("BTC-EUR", Side.BUY, 100.0, sig, stop_mode="pct"), _state(rc))
    assert d_pct.stop_price == pytest.approx(98.0) and d_pct.take_profit_price == pytest.approx(104.0)


def test_trailing_stop_only_rises_and_zero_levels_mean_none():
    rc = RiskController(PROFILES["medium"], Settings().costs)
    pos = Position("BTC-EUR", 1.0, 100.0, "x", "trendhold", stop_price=90.0, take_profit_price=0.0, trail_atr_k=2.0, atr_window=5)
    flat = [Candle(i * G, 100, 101, 99, 100, 1) for i in range(10)]
    assert rc.protective_exit(pos, Candle(99 * G, 100, 150, 99, 140, 1)) is None    # no take-profit at 0
    rising = flat + [Candle(10 * G, 100, 121, 100, 120, 1)]
    new = rc.trail_stop(pos, rising)
    assert new is not None and new > 90.0 and pos.stop_price == new
    falling = rising + [Candle(11 * G, 120, 120, 100, 101, 1)]
    assert rc.trail_stop(pos, falling) is None and pos.stop_price == new             # never lowered
    pos.trail_atr_k = 0.0
    assert rc.trail_stop(pos, rising) is None


def test_engine_records_trail_settings_and_trails_in_backtest():
    store = Store(":memory:")
    rows = make_candles(120, drift=0.0, vol=0.0005, seed=24) + make_candles(150, start_price=100, drift=0.01, vol=0.012, seed=25, t0=120 * G)
    store.save_candles("BTC-EUR", G, rows)
    s = Settings(products=["BTC-EUR"], granularity=G, history_candles=300)
    eng = Engine(s, store, PaperBroker(s.costs), build("trendhold"), RiskController(PROFILES["medium"], s.costs),
                 StoreDataSource(store, G), fx=None, mode="backtest", verbose=False)
    eng.init_state(datetime.fromtimestamp(rows[0].ts + G, tz=timezone.utc))
    for c in rows:
        try:
            eng.run_cycle(datetime.fromtimestamp(c.ts + G, tz=timezone.utc))
        except RuntimeError:
            pass
    assert store.count_events("fill") >= 1
    buys = [o for o in store.orders(100) if o["side"] == "BUY"]
    assert buys and buys[0]["take_profit_price"] == 0.0 and buys[0]["stop_price"] > 0
    if eng.state.positions:
        pos = eng.state.positions["BTC-EUR"]
        assert pos.trail_atr_k == PROFILES["medium"].atr_multiple
        assert store.count_events("trail") >= 1


def test_resample_aligns_and_drops_gaps():
    rows = make_candles(24, t0=0)                  # 24 x 5-minute candles from epoch 0
    out = resample(rows, G, G * 6)
    assert len(out) == 4 and out[0].ts == 0 and out[1].ts == G * 6
    assert out[0].open == rows[0].open and out[0].close == rows[5].close
    assert out[0].high == max(c.high for c in rows[:6]) and out[0].volume == pytest.approx(sum(c.volume for c in rows[:6]))
    gappy = rows[:5] + rows[6:]
    assert len(resample(gappy, G, G * 6)) == 3
    with pytest.raises(ValueError):
        resample(rows, G, G * 5 + 1)
