"""Maker/taker fees, urgent orders and the re-pricing retry path, exercised with a fake venue."""
from datetime import datetime, timezone, timedelta

import pytest

from conftest import G, make_candles
from trader.config import PROFILES, Costs, Settings
from trader.data import StoreDataSource
from trader.engine import Engine, candle_ts_from_order_id
from trader.execution.base import Broker
from trader.execution.paper import PaperBroker
from trader.models import Order, OrderStatus, Side, Signal
from trader.risk import RiskController
from trader.store import Store
from trader.strategies.base import Strategy


def test_costs_round_trip_is_maker_in_taker_out():
    c = Costs(fee_rate=0.006, taker_fee_rate=0.012, slippage_rate=0.001)
    assert c.round_trip == pytest.approx(0.006 + 0.012 + 0.002)


def test_paper_broker_charges_taker_only_on_urgent_orders(costs):
    b = PaperBroker(costs)
    normal = b.submit(Order("n", "BTC-EUR", Side.SELL, 1.0, 100.0))
    urgent = b.submit(Order("u", "BTC-EUR", Side.SELL, 1.0, 100.0, urgent=True))
    assert normal.fee == pytest.approx(99.9 * 0.006)
    assert urgent.fee == pytest.approx(99.9 * 0.012)


def test_order_id_parsing_handles_hyphenated_products_and_retries():
    assert candle_ts_from_order_id("live-BTC-EUR-1700000000-BUY") == 1700000000
    assert candle_ts_from_order_id("live-BTC-EUR-1700000000-BUY-r2") == 1700000000
    assert candle_ts_from_order_id("garbage") is None


class AlwaysBuy(Strategy):
    name = "always"
    warmup = 5

    def evaluate(self, product, candles, position):
        if position is None:
            return Signal(product, Side.BUY, self.name, 1.0, 0.08, "test buy")
        return self.hold(product)


class FlakyVenue(Broker):
    """Post-only orders never fill; marketable ones fill at once. Mimics a market running away."""
    mode = "live"

    def __init__(self, costs):
        self.costs = costs
        self.submitted: list[Order] = []

    def submit(self, order):
        self.submitted.append(order)
        order.status = OrderStatus.SUBMITTED
        if order.urgent:
            order.status, order.filled_qty, order.avg_fill_price = OrderStatus.FILLED, order.qty, order.price * 1.001
            order.fee = order.filled_qty * order.avg_fill_price * self.costs.taker_fee_rate
        return order

    def status(self, order):
        return order

    def cancel(self, order):
        if not order.is_done:
            order.status = OrderStatus.CANCELLED
        return order


def test_unfilled_post_only_order_is_repriced_then_goes_marketable():
    store = Store(":memory:")
    rows = make_candles(30, drift=0.0, vol=0.01, seed=31)
    store.save_candles("BTC-EUR", G, rows)
    s = Settings(products=["BTC-EUR"], granularity=G, history_candles=20, order_retries=2, order_timeout=60, halt_reset_days=0)
    venue = FlakyVenue(s.costs)
    eng = Engine(s, store, venue, AlwaysBuy(), RiskController(PROFILES["medium"], s.costs), StoreDataSource(store, G),
                 fx=None, mode="live", verbose=False)
    t0 = datetime.fromtimestamp(rows[-1].ts + G, tz=timezone.utc)
    eng.init_state(t0)
    eng.run_cycle(t0)                                   # attempt 0: post-only, stays open
    assert len(venue.submitted) == 1 and not venue.submitted[0].urgent
    assert store.is_processed("BTC-EUR", rows[-1].ts)
    eng.run_cycle(t0 + timedelta(seconds=120))          # reconcile: timed out -> cancelled -> candle re-opened, attempt 1
    assert len(venue.submitted) == 2 and venue.submitted[1].attempt == 1 and not venue.submitted[1].urgent
    eng.run_cycle(t0 + timedelta(seconds=300))          # attempt 2 = last: marketable, fills
    assert len(venue.submitted) == 3 and venue.submitted[2].urgent and venue.submitted[2].status == OrderStatus.FILLED
    assert "BTC-EUR" in eng.state.positions
    eng.run_cycle(t0 + timedelta(seconds=400))          # nothing more for this candle
    assert len(venue.submitted) == 3
    ids = [o.client_order_id for o in venue.submitted]
    assert ids[1].endswith("-r1") and ids[2].endswith("-r2") and len(set(ids)) == 3


def test_total_halt_pauses_then_resumes_rebased(now):
    from trader.models import PortfolioState, Position
    rc = RiskController(PROFILES["medium"], Costs())
    rc.halt_reset_days = 30
    st = PortfolioState(cash=0, initial_capital=1000)
    st.positions["BTC-EUR"] = Position("BTC-EUR", 10, 100, "x", "trendhold", 0, 0)
    st.last_prices["BTC-EUR"] = 100
    rc.roll_day(st, now)
    st.last_prices["BTC-EUR"] = 90
    assert rc.update_halts(st) and st.halted_total and st.halted_total_at == now.date().isoformat()
    rc.roll_day(st, now + timedelta(days=10))
    assert st.halted_total
    rc.roll_day(st, now + timedelta(days=30))
    assert not st.halted_total and st.initial_capital == pytest.approx(900)   # limit re-based on current equity
    rc.halt_reset_days = 0
    st.halted_total, st.halted_total_at = True, now.date().isoformat()
    rc.roll_day(st, now + timedelta(days=400))
    assert st.halted_total                                                   # manual restart only


def test_settings_read_the_new_env(monkeypatch):
    monkeypatch.setenv("TAKER_FEE_RATE", "0.0075")
    monkeypatch.setenv("HALT_RESET_DAYS", "45")
    monkeypatch.setenv("ORDER_RETRIES", "3")
    s = Settings.from_env()
    assert s.costs.taker_fee_rate == 0.0075 and s.halt_reset_days == 45 and s.order_retries == 3
