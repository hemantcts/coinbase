from datetime import datetime, timezone

import pytest

from trader.config import PROFILES, Costs, Settings
from trader.data import CoinbasePublic, StoreDataSource, source_granularity
from trader.engine import Engine
from trader.execution.paper import PaperBroker
from trader.models import Candle, Position, Side
from trader.risk import RiskController
from trader.store import Store
from trader.strategies import build
from trader.strategies.mtf import D1, H1, H6, M15, M30, MultiTimeframe

T0 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())


def _series(g: int, n: int, start: float, step: float, t0: int = T0) -> list[Candle]:
    """n candles of size g with a constant relative drift per candle."""
    rows, p = [], start
    for i in range(n):
        o, p = p, p * (1 + step)
        rows.append(Candle(t0 + i * g, o, max(o, p) * 1.002, min(o, p) * 0.998, p, 1.0))
    return rows


def _wavy(g: int, n: int, start: float, up: float, down: float) -> list[Candle]:
    """Rises on balance with a down candle every third step, so RSI sits in a normal range."""
    rows, p = [], start
    for i in range(n):
        o, p = p, p * (1 + (-down if i % 3 == 2 else up))
        rows.append(Candle(T0 + i * g, o, max(o, p) * 1.001, min(o, p) * 0.999, p, 1.0))
    return rows


def _uptrend_frames() -> dict[int, list[Candle]]:
    return {D1: _series(D1, 150, 50, 0.004), H6: _wavy(H6, 150, 80, 0.02, 0.015),
            H1: _wavy(H1, 150, 95, 0.003, 0.004), M30: _series(M30, 150, 98, 0.0003)}


def _dip_then_recover(n: int = 60) -> list[Candle]:
    """15m: steady rise, a short dip below the EMA, then the closing candle back above it."""
    rows = _series(M15, n - 6, 100, 0.0005)
    p = rows[-1].close
    for i, px in enumerate([p * 0.997, p * 0.994, p * 0.992, p * 0.991, p * 0.990]):
        rows.append(Candle(rows[-1].ts + M15, rows[-1].close, max(px, rows[-1].close), px, px, 1.0))
    rows.append(Candle(rows[-1].ts + M15, rows[-1].close, p * 1.01, rows[-1].close, p * 1.008, 1.0))
    return rows


# ---- data ------------------------------------------------------------------------------

def test_source_granularity_maps_derived_timeframes():
    assert source_granularity(900) == 900 and source_granularity(1800) == 900 and source_granularity(14400) == 3600
    with pytest.raises(ValueError):
        source_granularity(1234)


def test_store_source_resamples_missing_timeframes_and_hides_open_candles():
    store = Store(":memory:")
    store.save_candles("BTC-EUR", 900, _series(900, 400, 100, 0.001))
    store.save_candles("BTC-EUR", 21600, _series(21600, 40, 100, 0.001))
    src = StoreDataSource(store, 900)
    now = datetime.fromtimestamp(T0 + 400 * 900, tz=timezone.utc)
    m30 = src.closed_candles_at("BTC-EUR", 1000, now, 1800)
    assert len(m30) == 200 and all(c.ts % 1800 == 0 for c in m30)
    later = datetime.fromtimestamp(T0 + 40 * 21600, tz=timezone.utc)
    d1 = src.closed_candles_at("BTC-EUR", 50, later, 86400)
    assert len(d1) == 10 and d1[-1].ts + 86400 <= later.timestamp()
    early = datetime.fromtimestamp(T0 + 899, tz=timezone.utc)
    assert src.closed_candles_at("BTC-EUR", 10, early, 1800) == []


def test_live_source_reads_siblings_and_resamples(monkeypatch):
    calls = []

    def fake_candles(self, product, start=None, end=None):
        calls.append(self.granularity)
        return _series(self.granularity, 300, 100, 0.001)

    monkeypatch.setattr(CoinbasePublic, "candles", fake_candles)
    pub = CoinbasePublic(900)
    now = datetime.fromtimestamp(T0 + 300 * 3600, tz=timezone.utc)
    assert len(pub.closed_candles_at("BTC-EUR", 50, now, 3600)) == 50
    assert len(pub.closed_candles_at("BTC-EUR", 500, now, 1800)) == 150
    assert calls == [3600, 900]


# ---- strategy ------------------------------------------------------------------------------

def test_buys_only_when_every_timeframe_agrees_and_15m_triggers():
    sig = MultiTimeframe().evaluate("BTC-EUR", _dip_then_recover(), None, frames=_uptrend_frames())
    assert sig.side == Side.BUY and sig.strategy == "mtf" and sig.expected_move > 0
    h6 = _uptrend_frames()[H6]
    from trader import indicators as ind
    assert sig.atr == pytest.approx(ind.atr([c.high for c in h6], [c.low for c in h6], [c.close for c in h6], 14))


def test_no_buy_without_the_15m_trigger():
    steady = _series(M15, 60, 100, 0.0005)
    s = MultiTimeframe().evaluate("BTC-EUR", steady, None, frames=_uptrend_frames())
    assert s.side == Side.HOLD and "15m trigger" in s.reason


@pytest.mark.parametrize("g, label", [(D1, "1d"), (H6, "6h")])
def test_higher_timeframe_downtrend_blocks_entry(g, label):
    frames = _uptrend_frames()
    frames[g] = _series(g, 150, 100, -0.004)
    s = MultiTimeframe().evaluate("BTC-EUR", _dip_then_recover(), None, frames=frames)
    assert s.side == Side.HOLD and "no uptrend" in s.reason and label in s.reason


@pytest.mark.parametrize("g, label", [(H1, "1h"), (M30, "30m")])
def test_lower_timeframe_momentum_is_required(g, label):
    frames = _uptrend_frames()
    frames[g] = _series(g, 150, 100, -0.002)
    s = MultiTimeframe().evaluate("BTC-EUR", _dip_then_recover(), None, frames=frames)
    assert s.side == Side.HOLD and label in s.reason and "momentum" in s.reason


def test_exit_on_6h_cross_and_missing_frames_hold():
    frames = _uptrend_frames()
    pos = Position("BTC-EUR", 1.0, 100.0, "x", "mtf", 0.0, 0.0)
    assert MultiTimeframe().evaluate("BTC-EUR", _dip_then_recover(), pos, frames=frames).side == Side.HOLD
    frames[H6] = _series(H6, 150, 100, -0.004)
    assert MultiTimeframe().evaluate("BTC-EUR", _dip_then_recover(), pos, frames=frames).side == Side.SELL
    assert "timeframes" in MultiTimeframe().evaluate("BTC-EUR", _dip_then_recover(), None, frames={}).reason
    assert build("mtf").timeframes == (M30, H1, H6, D1)


# ---- engine ------------------------------------------------------------------------------

class _Frames:
    """Serves the uptrend frames and a 15m feed that triggers on its last candle."""

    def __init__(self):
        self.base = _dip_then_recover(60)
        shift = self.base[-1].ts + M15
        self.frames = {g: [Candle(c.ts - (rows[-1].ts + g) + shift, c.open, c.high, c.low, c.close, c.volume) for c in rows]
                       for g, rows in _uptrend_frames().items()}
        self.requested = []

    def closed_candles(self, product, n, now):
        return [c for c in self.base if c.ts + M15 <= now.timestamp()][-n:]

    def closed_candles_at(self, product, n, now, g):
        self.requested.append(g)
        return [c for c in self.frames[g] if c.ts + g <= now.timestamp()][-n:]


def test_engine_passes_frames_buys_and_trails_on_6h_atr():
    feed = _Frames()
    s = Settings(products=["BTC-EUR"], granularity=M15, report_currency="EUR")
    store = Store(":memory:")
    eng = Engine(s, store, PaperBroker(Costs()), build("mtf"), RiskController(PROFILES["client"], Costs()), feed,
                 mode="backtest", verbose=False)
    now = datetime.fromtimestamp(feed.base[-1].ts + M15, tz=timezone.utc)
    eng.run_cycle(now)
    pos = eng.state.positions["BTC-EUR"]
    assert pos.strategy == "mtf" and pos.trail_atr_k == PROFILES["client"].atr_multiple and pos.take_profit_price == 0
    assert set(feed.requested) == {M30, H1, H6, D1} and len(feed.requested) == 4      # once per cycle
    h6_atr = eng.last_signals["BTC-EUR"].atr
    assert pos.stop_price == pytest.approx(feed.base[-1].close - 6 * h6_atr, rel=1e-3)
