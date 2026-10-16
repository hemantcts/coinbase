from datetime import datetime, timezone, timedelta

import pytest

from conftest import G, T0, make_candles
from trader.backtest import run_backtest, walk_forward
from trader.config import PROFILES, Settings
from trader.data import StoreDataSource
from trader.engine import Engine
from trader.execution.paper import PaperBroker
from trader.models import Candle, Side, Signal
from trader.risk import RiskController
from trader.store import Store
from trader.strategies.base import Strategy


class AlwaysBuy(Strategy):
    """Buys on the first candle it sees, sells on the next: exercises the full path."""
    name = "always"
    warmup = 5

    def evaluate(self, product, candles, position):
        if position is None:
            return Signal(product, Side.BUY, self.name, 1.0, 0.05, "test buy")
        return Signal(product, Side.SELL, self.name, 1.0, 0.0, "test sell")


def _engine(store, strategy, products=("BTC-EUR",), profile="medium", history=50):
    s = Settings(products=list(products), granularity=G, history_candles=history)
    risk = RiskController(PROFILES[profile], s.costs)
    return Engine(s, store, PaperBroker(s.costs), strategy, risk, StoreDataSource(store, G), fx=None, mode="backtest", verbose=False)


def test_engine_is_idempotent_per_candle_and_restart_safe():
    store = Store(":memory:")
    rows = make_candles(60)
    store.save_candles("BTC-EUR", G, rows)
    eng = _engine(store, AlwaysBuy())
    t = datetime.fromtimestamp(rows[10].ts + G, tz=timezone.utc)
    eng.init_state(t)
    eng.run_cycle(t)
    eng.run_cycle(t)                                  # same candle again: nothing new
    assert store.count_events("fill") == 1
    # "Restart": a new engine on the same store must restore the position and not re-buy.
    eng2 = _engine(store, AlwaysBuy())
    eng2.init_state(t)
    assert "BTC-EUR" in eng2.state.positions
    eng2.run_cycle(t)
    assert store.count_events("fill") == 1
    eng2.run_cycle(datetime.fromtimestamp(rows[11].ts + G, tz=timezone.utc))
    assert store.count_events("fill") == 2 and "BTC-EUR" not in eng2.state.positions


def test_engine_never_uses_an_unclosed_candle():
    store = Store(":memory:")
    rows = make_candles(60)
    store.save_candles("BTC-EUR", G, rows)
    ds = StoreDataSource(store, G)
    now = datetime.fromtimestamp(rows[30].ts + G - 1, tz=timezone.utc)     # candle 30 still open
    got = ds.closed_candles("BTC-EUR", 10, now)
    assert got[-1].ts == rows[29].ts


def test_stop_loss_closes_position_and_records_loss():
    store = Store(":memory:")
    up = make_candles(30, drift=0.0, vol=0.0001, seed=2)
    crash = [Candle(up[-1].ts + G * (i + 1), 100, 100, 90, 92, 10) for i in range(3)]
    store.save_candles("BTC-EUR", G, up + crash)
    eng = _engine(store, AlwaysBuy())
    t_buy = datetime.fromtimestamp(up[-1].ts + G, tz=timezone.utc)
    eng.init_state(t_buy)
    eng.run_cycle(t_buy)
    assert "BTC-EUR" in eng.state.positions
    stop = eng.state.positions["BTC-EUR"].stop_price
    eng.run_cycle(datetime.fromtimestamp(crash[0].ts + G, tz=timezone.utc))
    fills = store.fills()
    assert fills[0]["side"] == "SELL" and "stop-loss" in fills[0]["reason"]
    assert fills[0]["avg_fill_price"] == pytest.approx(stop * (1 - eng.settings.costs.slippage_rate))
    assert "BTC-EUR" not in eng.state.positions and eng.state.realised_pnl < 0


def test_backtest_metrics_and_walk_forward():
    store = Store(":memory:")
    store.save_candles("BTC-EUR", G, make_candles(2000, drift=0.0001, vol=0.003, seed=5))
    start = datetime.fromtimestamp(T0, tz=timezone.utc)
    end = start + timedelta(seconds=2000 * G)
    r = run_backtest(store, ["BTC-EUR"], "ensemble", "high", start, end, capital=1000)
    assert r.cycles > 1500 and r.initial == 1000
    assert r.final == pytest.approx(1000 * (1 + r.net_return))
    assert 0 <= r.max_drawdown < 1 and r.buys >= r.sells
    assert len(r.equity_curve) == r.cycles
    ins, oos = walk_forward(store, ["BTC-EUR"], "trend", "medium", start, end)
    assert ins.end <= oos.start
    with pytest.raises(ValueError):
        run_backtest(store, ["XRP-EUR"], "trend", "medium", start, end)
