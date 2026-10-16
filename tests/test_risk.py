import pytest
from datetime import timedelta

from trader.config import PROFILES
from trader.models import Candle, OrderIntent, Position, Side, Signal
from trader.risk import ProductSpec, RiskController


def buy(product="BTC-EUR", price=100.0, move=0.03):
    sig = Signal(product, Side.BUY, "trend", 0.8, move)
    return OrderIntent(product, Side.BUY, price, sig)


def sell(product="BTC-EUR", price=100.0):
    sig = Signal(product, Side.SELL, "trend", 0.8, 0.0)
    return OrderIntent(product, Side.SELL, price, sig)


def test_position_cap_matches_brief(risk, state):
    d = risk.review(buy(), state)
    assert d.approved
    assert d.notional == pytest.approx(250.0, abs=0.01)         # CHF 250 of 1000
    assert d.stop_price == pytest.approx(98.0) and d.take_profit_price == pytest.approx(104.0)


def test_edge_filter_blocks_signals_that_cannot_beat_costs(risk, state, costs):
    d = risk.review(buy(move=costs.round_trip * 1.4), state)
    assert not d.approved and "expected move" in d.reason
    assert risk.review(buy(move=costs.round_trip * 1.6), state).approved


def test_exposure_and_reserve_limits(risk, state):
    state.positions["BTC-EUR"] = Position("BTC-EUR", 2.5, 100, "x", "trend", 98, 104)
    state.positions["ETH-EUR"] = Position("ETH-EUR", 2.5, 100, "x", "trend", 98, 104)
    state.last_prices = {"BTC-EUR": 100, "ETH-EUR": 100, "SOL-EUR": 100}
    state.cash = 500
    d = risk.review(buy("SOL-EUR"), state)
    assert d.approved and d.notional == pytest.approx(100.0, abs=0.01)   # only 100 of exposure room left
    state.cash = 405
    d = risk.review(buy("SOL-EUR"), state)
    assert not d.approved and "below minimum order" in d.reason            # reserve of 400 protects


def test_halts_block_buys_but_allow_exits(risk, state):
    state.positions["BTC-EUR"] = Position("BTC-EUR", 1.0, 100, "x", "trend", 98, 104)
    state.last_prices["BTC-EUR"] = 100
    state.halted_daily = True
    assert not risk.review(buy("ETH-EUR"), state).approved
    assert risk.review(sell(), state).approved
    state.halted_daily, state.halted_total = False, True
    assert not risk.review(buy("ETH-EUR"), state).approved
    assert risk.review(sell(), state).approved
    risk.manual_stop = True
    state.halted_total = False
    assert not risk.review(buy("ETH-EUR"), state).approved
    assert risk.review(sell(), state).approved


def test_daily_and_total_loss_limits_trigger(risk, state, now):
    state.positions["BTC-EUR"] = Position("BTC-EUR", 6.0, 100, "x", "trend", 0, 1e9)
    state.cash = 400
    state.last_prices["BTC-EUR"] = 100
    assert risk.update_halts(state) == []
    state.last_prices["BTC-EUR"] = 96.5           # equity 979 -> daily -2.1%
    ev = risk.update_halts(state)
    assert state.halted_daily and not state.halted_total and "DAILY" in ev[0]
    state.last_prices["BTC-EUR"] = 86             # equity 916 -> total -8.4%
    ev = risk.update_halts(state)
    assert state.halted_total and "TOTAL" in ev[0]
    # New day clears the daily halt, never the total halt.
    assert risk.roll_day(state, now + timedelta(days=1))
    assert not state.halted_daily and state.halted_total and state.new_positions_today == 0


def test_max_new_positions_per_day(risk, state):
    state.new_positions_today = 4
    assert "per day" in risk.review(buy(), state).reason


def test_exchange_rounding_and_minimums(costs, state):
    spec = ProductSpec("BTC-EUR", base_increment=0.001, base_min_size=0.001, quote_increment=0.5)
    rc = RiskController(PROFILES["medium"], costs, {"BTC-EUR": spec})
    d = rc.review(buy(price=30000.0), state)
    assert d.approved and abs(d.qty * 1000 - round(d.qty * 1000)) < 1e-6
    assert d.stop_price % 0.5 == 0
    spec_big = ProductSpec("BTC-EUR", base_increment=0.001, base_min_size=1.0)
    rc = RiskController(PROFILES["medium"], costs, {"BTC-EUR": spec_big})
    assert "below exchange minimum" in rc.review(buy(price=30000.0), state).reason


def test_protective_exits(risk):
    pos = Position("BTC-EUR", 1.0, 100, "x", "trend", stop_price=98.0, take_profit_price=104.0)
    assert risk.protective_exit(pos, Candle(0, 100, 101, 99, 100, 1)) is None
    stop = risk.protective_exit(pos, Candle(0, 100, 100, 97.5, 98.5, 1))
    assert stop and stop.side == Side.SELL and stop.price == 98.0 and "stop-loss" in stop.reason
    tp = risk.protective_exit(pos, Candle(0, 100, 104.5, 99, 103, 1))
    assert tp and tp.price == 104.0 and "take-profit" in tp.reason


def test_profiles_scale_with_capital():
    for p in PROFILES.values():
        a = p.amounts(2000)
        assert a["max_position"] == p.max_position_fraction * 2000
        assert p.max_exposure_fraction + p.min_reserve_fraction == pytest.approx(1.0)
