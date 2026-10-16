import pytest

from trader.execution.paper import PaperBroker
from trader.models import Decision, Order, OrderStatus, Side
from trader.portfolio import apply_fill


def test_paper_fill_math(costs, state):
    broker = PaperBroker(costs)
    o = broker.submit(Order("a", "BTC-EUR", Side.BUY, 2.0, 100.0, created_at="t", updated_at="t"))
    assert o.status == OrderStatus.FILLED
    assert o.avg_fill_price == pytest.approx(100.1)
    assert o.fee == pytest.approx(2.0 * 100.1 * 0.006)
    pnl = apply_fill(state, o, Decision(True, "ok", qty=2.0, stop_price=98, take_profit_price=104))
    assert pnl == 0 and state.cash == pytest.approx(1000 - 200.2 - o.fee)
    assert state.positions["BTC-EUR"].qty == 2.0 and state.new_positions_today == 1

    s = broker.submit(Order("b", "BTC-EUR", Side.SELL, 2.0, 110.0, created_at="t", updated_at="t"))
    assert s.avg_fill_price == pytest.approx(109.89)
    pnl = apply_fill(state, s)
    assert pnl == pytest.approx(2 * (109.89 - 100.1) - s.fee - o.fee)
    assert "BTC-EUR" not in state.positions
    assert state.realised_pnl == pytest.approx(pnl)
    assert state.equity() == pytest.approx(1000 + pnl)


def test_paper_broker_is_idempotent_per_client_id(costs):
    broker = PaperBroker(costs)
    a = broker.submit(Order("same", "BTC-EUR", Side.BUY, 1.0, 100.0))
    b = broker.submit(Order("same", "BTC-EUR", Side.BUY, 5.0, 999.0))
    assert a is b and b.qty == 1.0


def test_paper_rejects_bad_orders(costs):
    o = PaperBroker(costs).submit(Order("x", "BTC-EUR", Side.BUY, 0.0, 100.0))
    assert o.status == OrderStatus.REJECTED
