import json
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from trader.config import Settings
from trader.dashboard import create_app
from trader.dashboard.overview import bot_status
from trader.models import Order, OrderStatus, PortfolioState, Position, Side, iso, utcnow
from trader.store import Store

H = {"X-Token": "t"}


def test_bot_status_rules():
    st = PortfolioState(cash=1000, initial_capital=1000)
    assert bot_status(False, False, None, False, False, "", True) == ("STOPPED", "never_ran")
    assert bot_status(True, True, st, False, False, "", True) == ("STOPPED", "no_recent_cycle")
    assert bot_status(False, True, st, True, False, "", True) == ("PAUSED", "manual_stop")
    assert bot_status(False, True, st, False, True, "", True) == ("PAUSED", "flatten")
    assert bot_status(False, True, st, False, False, "hack", True) == ("PAUSED", "severe_event")
    assert bot_status(False, True, st, False, False, "", False) == ("PAUSED", "stale_data")
    st.halted_daily = True
    assert bot_status(False, True, st, False, False, "", True) == ("PAUSED", "halt_daily")
    st.halted_daily, st.halted_total = False, True
    assert bot_status(False, True, st, False, False, "", True) == ("PAUSED", "halt_total")
    assert bot_status(False, True, PortfolioState(cash=1, initial_capital=1), False, False, "", True) == ("RUNNING", "ok")


def _seeded(tmp_path: Path) -> Settings:
    s = Settings(data_dir=tmp_path, dashboard_token="t")
    store = Store(s.database)
    now = utcnow()
    st = PortfolioState(cash=900, initial_capital=1000, last_prices={"BTC-EUR": 110.0, "ETH-EUR": 50.0})
    st.positions["BTC-EUR"] = Position("BTC-EUR", 1.0, 100.0, iso(now), "adaptive:dca", 82.0, 106.0, dca_level=1)
    store.save_state(st, iso(now))
    store.set_meta("last_cycle_at", iso(now))
    store.set_meta("data_age_seconds", "60")
    store.set_meta("products", "BTC-EUR,ETH-EUR")
    store.set_meta("watch_products", "SOL-EUR,XRP-EUR")
    store.set_meta("last_signals", json.dumps({
        "ETH-EUR": {"side": "HOLD", "strategy": "adaptive", "reason": "no entry: downtrend", "price": 50.0, "traded": True},
        "SOL-EUR": {"side": "BUY", "strategy": "adaptive:trend", "reason": "trend entry", "price": 80.0, "traded": False},
        "XRP-EUR": {"side": "HOLD", "strategy": "adaptive", "reason": "no entry", "price": 1.1, "traded": False}}))
    store.save_order(Order("x-sell", "ETH-EUR", Side.SELL, 1, 50, OrderStatus.FILLED, iso(now - timedelta(days=1)), iso(now - timedelta(days=1))), 3.0)
    store.save_order(Order("x-open", "ETH-EUR", Side.BUY, 1, 50, OrderStatus.SUBMITTED, iso(now), iso(now)))
    store.log(iso(now), "signal", {"product": "SOL-EUR", "side": "BUY", "strategy": "adaptive:trend", "reason": "trend entry"})
    (tmp_path / "universe.json").write_text(json.dumps({"ts": iso(now), "coins": [
        {"product": p, "score": 1 - i / 10} for i, p in enumerate(["BTC-EUR", "ETH-EUR", "SOL-EUR", "XRP-EUR"])]}))
    return s


def test_state_reports_status_counts_coins_and_last_signal(tmp_path: Path):
    bot = TestClient(create_app(_seeded(tmp_path))).get("/api/state", headers=H).json()["bot"]
    assert bot["status"] == "RUNNING" and bot["activity"] == "executing"
    assert bot["counts"] == {"completed": 1, "active": 1, "pending": 1, "markets": 2, "watched": 2}
    assert [c["product"] for c in bot["coins"]] == ["BTC-EUR", "ETH-EUR", "SOL-EUR", "XRP-EUR"]
    btc = bot["coins"][0]
    assert btc["in_trade"] and btc["play"] == "adaptive:dca" and btc["pnl_pct"] > 0.09 and btc["traded"]
    sol = bot["coins"][2]
    assert not sol["traded"] and sol["side"] == "BUY" and sol["rank"] == 3
    assert bot["last_signal"]["product"] == "SOL-EUR" and bot["last_activity_at"]
    assert bot["active_plays"] == ["adaptive:dca"]


def test_state_reports_paused_and_stopped(tmp_path: Path):
    s = _seeded(tmp_path)
    s.stop_file.touch()
    c = TestClient(create_app(s))
    assert c.get("/api/state", headers=H).json()["bot"]["status"] == "PAUSED"
    s.stop_file.unlink()
    Store(s.database).set_meta("last_cycle_at", iso(utcnow() - timedelta(hours=2)))
    bot = c.get("/api/state", headers=H).json()["bot"]
    assert bot["status"] == "STOPPED" and bot["activity"] == "stopped"


def test_empty_database_is_stopped_not_an_error(tmp_path: Path):
    bot = TestClient(create_app(Settings(data_dir=tmp_path, dashboard_token="t"))).get("/api/state", headers=H).json()["bot"]
    assert bot["status"] == "STOPPED" and bot["status_reason"] == "never_ran" and bot["counts"]["completed"] == 0


def test_page_has_the_status_and_markets_sections(tmp_path: Path):
    html = TestClient(create_app(Settings(data_dir=tmp_path, dashboard_token="t"))).get("/").text
    for marker in ('id="light"', 'id="facts"', 'id="markets"', "RUNNING", "PAUSED", "STOPPED"):
        assert marker in html


def test_watched_coins_get_signals_but_never_orders(tmp_path: Path):
    from datetime import datetime, timezone
    from conftest import G, T0
    from trader.config import Costs
    from trader.engine import Engine
    from trader.execution.paper import PaperBroker
    from trader.models import Candle, Signal
    from trader.risk import RiskController
    from trader.config import PROFILES

    class AlwaysBuy:
        name, warmup, stop_mode, atr_window = "stub", 1, "pct", 14

        def evaluate(self, product, candles, position):
            return Signal(product, Side.BUY, "stub", 1.0, 0.2, "buy", 1.0) if position is None else Signal.hold(product, "stub")

    class Feed:
        def closed_candles(self, product, n, now):
            return [Candle(T0 + i * G, 100, 100, 100, 100, 1) for i in range(3) if T0 + (i + 1) * G <= now.timestamp()]

    s = Settings(products=["BTC-EUR"], granularity=G, data_dir=tmp_path, report_currency="EUR", watch_top_n=5)
    store = Store(":memory:")
    eng = Engine(s, store, PaperBroker(Costs()), AlwaysBuy(), RiskController(PROFILES["client"], Costs()), Feed(),
                 mode="paper", verbose=False)
    now = datetime.fromtimestamp(T0 + 3 * G, tz=timezone.utc)
    eng.run_cycle(now)                          # universe lookup fails offline and falls back: nothing watched yet
    eng.watch_products = ["SOL-EUR"]
    eng.run_cycle(now)
    assert {o["product"] for o in store.orders()} == {"BTC-EUR"}
    sig = json.loads(store.get_meta("last_signals"))
    assert sig["SOL-EUR"]["side"] == "BUY" and sig["SOL-EUR"]["traded"] is False and sig["BTC-EUR"]["traded"] is True
