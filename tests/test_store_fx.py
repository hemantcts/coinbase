from datetime import datetime, timezone

import pytest

from conftest import make_candles
from trader.fx import FxService
from trader.models import Order, OrderStatus, PortfolioState, Position, Side
from trader.store import Store


def test_state_round_trip(store):
    st = PortfolioState(cash=700, initial_capital=1000, fx_rate=1.05, fx_rate_ts="2026-10-16T00:00:00+00:00")
    st.positions["BTC-EUR"] = Position("BTC-EUR", 0.01, 30000, "2026-10-16T00:00:00+00:00", "trend", 29400, 31200, 1.8)
    st.last_prices["BTC-EUR"] = 30500
    st.day, st.day_start_equity, st.new_positions_today = "2026-10-16", 1000, 2
    store.save_state(st, "2026-10-16T00:05:00+00:00", report_equity=st.equity() / 1.05)
    back = store.load_state()
    assert back.cash == 700 and back.positions["BTC-EUR"].stop_price == 29400
    assert back.equity() == pytest.approx(st.equity())           # prices restored: no zero-valued holdings
    assert back.new_positions_today == 2 and back.fx_rate == 1.05
    snaps = store.snapshots()
    assert len(snaps) == 1 and snaps[0]["report_equity"] == pytest.approx(st.equity() / 1.05)


def test_orders_and_processed(store):
    o = Order("paper-BTC-EUR-1-BUY", "BTC-EUR", Side.BUY, 1, 100, OrderStatus.NEW, "t", "t", strategy="trend", stop_price=98, take_profit_price=104)
    store.save_order(o)
    assert store.open_orders()[0].client_order_id == o.client_order_id
    o.status, o.filled_qty, o.avg_fill_price = OrderStatus.FILLED, 1, 100.1
    store.save_order(o, pnl=0.0)
    assert store.open_orders() == [] and store.get_order(o.client_order_id).take_profit_price == 104
    assert not store.is_processed("BTC-EUR", 1)
    store.mark_processed("BTC-EUR", 1)
    assert store.is_processed("BTC-EUR", 1)


def test_candles_cache_and_range(store):
    rows = make_candles(50)
    assert store.save_candles("BTC-EUR", 300, rows) == 50
    store.save_candles("BTC-EUR", 300, rows[:10])          # idempotent
    a, b, n = store.candle_range("BTC-EUR", 300)
    assert n == 50 and a == rows[0].ts and b == rows[-1].ts
    assert store.candles("BTC-EUR", 300, limit=5, newest_first=True)[-1].ts == rows[-1].ts


def test_fx_uses_store_cache_then_fallback(store):
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)
    fx = FxService(store, fallback_rate=1.05, offline=True)
    r = fx.rate(now)
    assert r.rate == 1.05 and r.source == "fallback"
    assert fx.to_report(105.0, now) == pytest.approx(100.0)
    fx2 = FxService(store, fallback_rate=None, offline=True)
    assert fx2.rate(now).rate == 1.05                      # read back from the store
    with pytest.raises(RuntimeError):
        FxService(Store(":memory:"), fallback_rate=None, offline=True).rate(now)


def test_fx_identity_when_currencies_match():
    fx = FxService(None, None, base="EUR", quote="EUR", offline=True)
    assert fx.rate().rate == 1.0
