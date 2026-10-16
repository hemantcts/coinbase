"""Failure cases from the client's plan: stale data, price gaps, API outages, restarts, duplicate orders."""
from datetime import datetime, timezone, timedelta

import pytest

from conftest import G, make_candles
from trader.config import PROFILES, Settings
from trader.data import StoreDataSource
from trader.engine import Engine
from trader.execution.paper import PaperBroker
from trader.models import Candle, OrderIntent, Position, Side, Signal
from trader.risk import RiskController
from trader.store import Store
from trader.strategies.base import Strategy


class AlwaysBuy(Strategy):
    name = "always"
    warmup = 5

    def evaluate(self, product, candles, position):
        if position is None:
            return Signal(product, Side.BUY, self.name, 1.0, 0.08, "test buy")
        return self.hold(product)


class FlakySource(StoreDataSource):
    """Raises on demand to simulate an API outage."""
    fail = False

    def closed_candles(self, product, n, now):
        if self.fail:
            raise ConnectionError("coinbase down")
        return super().closed_candles(product, n, now)


def _engine(store, mode="paper", tmp_path=None, **kw):
    s = Settings(products=["BTC-EUR"], granularity=G, history_candles=20, halt_reset_days=0, data_dir=tmp_path, **kw)
    src = FlakySource(store, G)
    risk = RiskController(PROFILES["client"], s.costs)
    return Engine(s, store, PaperBroker(s.costs), AlwaysBuy(), risk, src, fx=None, mode=mode, verbose=False), src


def test_client_profile_matches_the_plan():
    p = PROFILES["client"]
    a = p.amounts(1000)
    assert a["max_position"] == 100 and a["max_exposure"] == 300 and a["daily_loss_limit"] == 20 and a["total_loss_limit"] == 80
    assert p.max_exposure_fraction + p.min_reserve_fraction == pytest.approx(1.0)


def test_stale_market_data_blocks_new_buys_but_not_exits(tmp_path):
    store = Store(":memory:")
    rows = make_candles(30, vol=0.01, seed=41)
    store.save_candles("BTC-EUR", G, rows)
    eng, _ = _engine(store, tmp_path=tmp_path)
    late = datetime.fromtimestamp(rows[-1].ts + G + 3 * G, tz=timezone.utc)      # newest candle closed 3 candles ago
    eng.init_state(late)
    eng.run_cycle(late)
    assert eng.risk.stale_data is True
    assert store.count_events("stale_data") == 1 and store.count_events("fill") == 0
    assert any("stale" in e["detail"] for e in store.events(kind="rejected"))
    # Exits still go through while data is stale.
    rc = eng.risk
    st = eng.state
    st.positions["BTC-EUR"] = Position("BTC-EUR", 1.0, 100, "x", "always", 0, 0)
    assert rc.review(OrderIntent("BTC-EUR", Side.SELL, 100.0, Signal("BTC-EUR", Side.SELL, "always")), st).approved
    # Fresh data clears the flag.
    fresh = datetime.fromtimestamp(rows[-1].ts + G + 60, tz=timezone.utc)
    eng.state.positions.clear()
    eng.run_cycle(fresh)
    assert eng.risk.stale_data is False


def test_gap_through_the_stop_fills_at_the_open_not_the_stop(risk):
    pos = Position("BTC-EUR", 1.0, 100, "x", "trendhold", stop_price=95.0, take_profit_price=0.0)
    gap = Candle(0, 90.0, 91.0, 88.0, 89.0, 1)        # opened far below the stop
    intent = risk.protective_exit(pos, gap)
    assert intent is not None and intent.price == pytest.approx(90.0) and "gap" in intent.reason
    normal = Candle(0, 96.0, 96.5, 94.0, 95.5, 1)     # traded through the stop within the candle
    assert risk.protective_exit(pos, normal).price == pytest.approx(95.0)


def test_api_outage_logs_an_error_and_leaves_state_intact(tmp_path):
    store = Store(":memory:")
    rows = make_candles(30, vol=0.01, seed=42)
    store.save_candles("BTC-EUR", G, rows)
    eng, src = _engine(store, tmp_path=tmp_path)
    t = datetime.fromtimestamp(rows[-1].ts + G, tz=timezone.utc)
    eng.init_state(t)
    eng.run_cycle(t)
    cash_before, pos_before = eng.state.cash, dict(eng.state.positions)
    src.fail = True
    with pytest.raises(RuntimeError, match="no market data"):
        eng.run_cycle(t + timedelta(seconds=300))
    assert store.count_events("error") == 1
    assert eng.state.cash == cash_before and eng.state.positions == pos_before
    src.fail = False
    eng.run_cycle(t + timedelta(seconds=600))          # recovers without duplicating the position
    assert store.count_events("fill") == 1


def test_restart_during_open_order_does_not_duplicate(tmp_path):
    store = Store(":memory:")
    rows = make_candles(30, vol=0.01, seed=43)
    store.save_candles("BTC-EUR", G, rows)
    eng, _ = _engine(store, tmp_path=tmp_path)
    t = datetime.fromtimestamp(rows[-1].ts + G, tz=timezone.utc)
    eng.init_state(t)
    eng.run_cycle(t)
    assert store.count_events("fill") == 1
    for _ in range(3):                                  # three "restarts" on the same candle
        e2, _ = _engine(store, tmp_path=tmp_path)
        e2.init_state(t)
        e2.run_cycle(t)
    assert store.count_events("fill") == 1 and len(store.fills()) == 1
