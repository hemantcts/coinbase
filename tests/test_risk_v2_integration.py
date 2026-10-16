"""The client's risk_v2 ideas, wired into the real risk controller: peak drawdown tripwire and evidence."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import G, make_candles
from trader.config import PROFILES, Costs, Settings
from trader.data import StoreDataSource
from trader.engine import Engine
from trader.execution.paper import PaperBroker
from trader.models import Evidence, OrderIntent, PortfolioState, Position, Side, Signal
from trader.risk import RiskController
from trader.store import Store
from trader.strategies.base import Strategy

N = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def _buy(product="BTC-EUR"):
    return OrderIntent(product, Side.BUY, 100.0, Signal(product, Side.BUY, "trendhold", 0.8, 0.05))


def test_peak_drawdown_tripwire_pauses_and_rebases():
    rc = RiskController(PROFILES["client"], Costs())
    rc.halt_reset_days = 30
    st = PortfolioState(cash=0, initial_capital=1000)
    st.positions["BTC-EUR"] = Position("BTC-EUR", 10, 100, "x", "trendhold", 0, 0)
    st.last_prices["BTC-EUR"] = 100
    rc.roll_day(st, N)
    st.last_prices["BTC-EUR"] = 130                      # run up: peak 1300
    assert rc.update_halts(st) == [] and st.peak_equity == pytest.approx(1300)
    st.last_prices["BTC-EUR"] = 121                      # 1210: 6.9 % below peak, still above start -> fine
    assert rc.update_halts(st) == []
    st.last_prices["BTC-EUR"] = 119                      # 1190: 8.5 % below peak although +19 % since start
    ev = rc.update_halts(st)
    assert st.halted_total and "PEAK DRAWDOWN" in ev[0]
    assert not rc.review(_buy("ETH-EUR"), st).approved
    assert rc.review(OrderIntent("BTC-EUR", Side.SELL, 119.0, Signal("BTC-EUR", Side.SELL, "x")), st).approved
    rc.roll_day(st, N + timedelta(days=30))
    assert not st.halted_total and st.peak_equity == pytest.approx(1190) and st.initial_capital == pytest.approx(1190)


def test_profiles_without_tripwire_ignore_peak():
    rc = RiskController(PROFILES["medium"], Costs())
    st = PortfolioState(cash=0, initial_capital=1000)
    st.positions["BTC-EUR"] = Position("BTC-EUR", 10, 100, "x", "trendhold", 0, 0)
    st.last_prices["BTC-EUR"] = 150
    rc.roll_day(st, N); rc.update_halts(st)
    st.last_prices["BTC-EUR"] = 120                      # 20 % off the peak, +20 % since start
    events = rc.update_halts(st)
    assert not st.halted_total and not any("PEAK" in e for e in events)   # (the daily limit may fire; that is separate)


def test_evidence_blocks_only_verified_severe_recent_events():
    rc = RiskController(PROFILES["client"], Costs())
    st = PortfolioState(cash=1000, initial_capital=1000); rc.roll_day(st, N)
    ok = lambda: rc.review(_buy(), st).approved
    assert rc.apply_evidence([], N) == "" and ok()
    hack = Evidence("exchange-status", (N - timedelta(hours=2)).isoformat(), True, 0.9, "security")
    assert "security" in rc.apply_evidence([hack], N) and not ok()
    assert rc.apply_evidence([Evidence("x", (N - timedelta(hours=2)).isoformat(), False, 0.9, "security")], N) == "" and ok()   # unverified
    assert rc.apply_evidence([Evidence("x", (N - timedelta(hours=2)).isoformat(), True, 0.4, "macro")], N) == "" and ok()       # mild
    assert rc.apply_evidence([Evidence("x", (N - timedelta(hours=30)).isoformat(), True, 0.9, "security")], N) == "" and ok()  # old
    assert rc.apply_evidence([Evidence("x", (N + timedelta(hours=1)).isoformat(), True, 0.9, "security")], N) == "" and ok()   # future: ignored
    assert rc.apply_evidence([Evidence("x", "2026-10-16T10:00:00", True, 0.9, "security")], N) == "" and ok()                  # no timezone: ignored


class AlwaysBuy(Strategy):
    name = "always"
    warmup = 5

    def evaluate(self, product, candles, position):
        return Signal(product, Side.BUY, self.name, 1.0, 0.08, "test") if position is None else self.hold(product)


def test_engine_reads_evidence_file(tmp_path):
    store = Store(":memory:")
    rows = make_candles(30, vol=0.01, seed=51)
    store.save_candles("BTC-EUR", G, rows)
    s = Settings(products=["BTC-EUR"], granularity=G, history_candles=20, data_dir=tmp_path, halt_reset_days=0)
    eng = Engine(s, store, PaperBroker(s.costs), AlwaysBuy(), RiskController(PROFILES["client"], s.costs),
                 StoreDataSource(store, G), fx=None, mode="paper", verbose=False)
    t = datetime.fromtimestamp(rows[-1].ts + G, tz=timezone.utc)
    (tmp_path / "evidence.json").write_text(json.dumps([{"source": "regulator", "published_at": (t - timedelta(hours=1)).isoformat(),
                                                        "verified": True, "severity": 0.8, "category": "regulation", "note": "test"}]))
    eng.init_state(t)
    eng.run_cycle(t)
    assert store.count_events("fill") == 0 and store.count_events("severe_event") == 1
    assert "regulation" in (store.get_meta("severe_event") or "")
    (tmp_path / "evidence.json").write_text("[]")
    eng.run_cycle(t + timedelta(seconds=300))
    assert eng.risk.severe_event == "" and store.get_meta("severe_event") == ""
