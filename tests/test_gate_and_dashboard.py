import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trader.backtest import BacktestResult
from trader.config import PROFILES, Settings
from trader.dashboard import create_app
from trader.gate import backtest_checks, gate_status, paper_checks
from trader.models import PortfolioState
from trader.store import Store


def _result(**kw) -> BacktestResult:
    base = dict(strategy="ensemble", profile="medium", products=["BTC-EUR"], start="a", end="b", days=30, cycles=100,
                initial=1000, final=1030, net_return=0.03, benchmark_return=0.05, max_drawdown=0.03, max_loss_from_start=0.02, round_trips=8, buys=8, sells=8, win_rate=0.5,
                profit_factor=1.3, gross_profit=60, gross_loss=30, fees=20, fee_drag=0.02, avg_trade_pnl=3, sharpe=1,
                exposure_pct=0.3, halted_total=False, daily_halts=0, stop_outs=2, take_profits=3)
    base.update(kw)
    return BacktestResult(**base)


def test_backtest_checks():
    assert all(c.passed for c in backtest_checks(_result(), PROFILES["medium"]))
    # Lost less than the market: passes the first check even though the return is negative.
    bear = backtest_checks(_result(net_return=-0.02, benchmark_return=-0.13), PROFILES["medium"])
    assert next(c for c in bear if c.name == "oos_profit_or_beats_market").passed
    bad = backtest_checks(_result(net_return=-0.01, benchmark_return=0.02, max_loss_from_start=0.1, round_trips=2, halted_total=True, fees=50), PROFILES["medium"])
    assert [c.name for c in bad if not c.passed] == ["oos_profit_or_beats_market", "oos_loss_within_limit", "oos_enough_trades",
                                                     "oos_not_halted", "fees_below_half_of_gross_profit"]


def _paper_store(hours: float, gap_minutes: float = 5, errors: int = 0) -> Store:
    store = Store(":memory:")
    st = PortfolioState(cash=1000, initial_capital=1000)
    t = datetime(2026, 10, 10, tzinfo=timezone.utc)
    n = int(hours * 60 / gap_minutes)
    for i in range(n):
        store.save_state(st, (t + timedelta(minutes=gap_minutes * i)).isoformat(timespec="seconds"))
    for i in range(errors):
        store.log(t.isoformat(), "error", "boom")
    store.set_meta("fx_source_at_start", "frankfurter:2026-10-10")
    store.set_meta("mode", "paper")
    return store


def test_paper_checks_pass_after_72h_and_fail_when_short_or_gappy():
    s = Settings(poll_seconds=300)
    store = _paper_store(80)
    last = datetime.fromisoformat(store.snapshots()[-1]["ts"])
    checks = paper_checks(store, s, now=last + timedelta(minutes=5))
    assert all(c.passed for c in checks), [c for c in checks if not c.passed]
    short = paper_checks(_paper_store(10), s, now=datetime(2026, 10, 10, 10, 5, tzinfo=timezone.utc))
    assert not next(c for c in short if c.name == "paper_duration").passed
    gappy = _paper_store(80, gap_minutes=45)
    assert not next(c for c in paper_checks(gappy, s, now=last) if c.name == "paper_continuity").passed
    noisy = _paper_store(80, errors=100)
    assert not next(c for c in paper_checks(noisy, s, now=last + timedelta(minutes=5)) if c.name == "paper_error_rate").passed


def test_gate_status_requires_fresh_matching_pass(tmp_path: Path):
    s = Settings(data_dir=tmp_path, strategy="ensemble", risk_profile="medium")
    assert not gate_status(s)[0]
    s.gate_file.write_text(json.dumps({"passed": True, "ts": datetime.now(timezone.utc).isoformat(), "strategy": "ensemble", "profile": "medium"}))
    assert gate_status(s)[0]
    s.gate_file.write_text(json.dumps({"passed": True, "ts": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(), "strategy": "ensemble", "profile": "medium"}))
    assert "older" in gate_status(s)[1]
    s.gate_file.write_text(json.dumps({"passed": True, "ts": datetime.now(timezone.utc).isoformat(), "strategy": "trend", "profile": "medium"}))
    assert "configured" in gate_status(s)[1]
    s.gate_file.write_text(json.dumps({"passed": False, "ts": datetime.now(timezone.utc).isoformat(), "checks": [{"name": "x", "passed": False}]}))
    assert "FAILED" in gate_status(s)[1]


def test_live_arming_requires_everything(tmp_path: Path):
    from trader.execution.live import ARMING_SENTENCE, assert_armed
    s = Settings(data_dir=tmp_path, live_trading=False)
    with pytest.raises(RuntimeError, match="LIVE_TRADING"):
        assert_armed(s)
    s.live_trading = True
    with pytest.raises(RuntimeError, match="arming sentence"):
        assert_armed(s)
    s.live_armed_file.write_text(ARMING_SENTENCE)
    with pytest.raises(RuntimeError, match="gate"):
        assert_armed(s)
    s.gate_file.write_text(json.dumps({"passed": True, "ts": datetime.now(timezone.utc).isoformat(), "strategy": s.strategy, "profile": s.risk_profile}))
    with pytest.raises(RuntimeError, match="COINBASE_API_KEY"):
        assert_armed(s)


def test_dashboard_auth_and_controls(tmp_path: Path):
    s = Settings(data_dir=tmp_path, dashboard_token="secret")
    client = TestClient(create_app(s))
    assert client.get("/").status_code == 200
    assert client.get("/api/state").status_code == 401
    r = client.get("/api/state", headers={"X-Token": "secret"})
    assert r.status_code == 200 and r.json()["state"] is None and r.json()["health"]["manual_stop"] is False
    assert client.post("/api/control/stop", headers={"X-Token": "secret"}).json()["manual_stop"] is True
    assert s.stop_file.exists()
    assert client.post("/api/control/resume", headers={"X-Token": "secret"}).json()["manual_stop"] is False
    assert client.post("/api/control/nope", headers={"X-Token": "secret"}).status_code == 404
    open_client = TestClient(create_app(Settings(data_dir=tmp_path)))
    assert open_client.post("/api/control/stop").status_code == 403


def test_dashboard_login_protects_everything_and_blocks_indexing(tmp_path: Path):
    import base64
    s = Settings(data_dir=tmp_path, dashboard_user="admin", dashboard_password="pw-123", dashboard_token="tok")
    c = TestClient(create_app(s))
    r = c.get("/")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic") and "Trading" not in r.text
    assert c.get("/api/state").status_code == 401
    bad = {"Authorization": "Basic " + base64.b64encode(b"admin:wrong").decode()}
    assert c.get("/", headers=bad).status_code == 401
    good = {"Authorization": "Basic " + base64.b64encode(b"admin:pw-123").decode()}
    r = c.get("/", headers=good)
    assert r.status_code == 200 and "noindex" in r.text and r.headers["x-robots-tag"].startswith("noindex")
    st = c.get("/api/state", headers=good)
    assert st.status_code == 200 and st.json()["auth_mode"] == "basic"
    assert c.post("/api/control/stop", headers=good).status_code == 200
    assert c.get("/api/state", headers={"X-Token": "tok"}).status_code == 200         # token still works for scripts
    assert c.get("/robots.txt").status_code == 200 and "Disallow: /" in c.get("/robots.txt").text


def test_viewer_login_sees_data_but_cannot_control(tmp_path: Path):
    import base64
    s = Settings(data_dir=tmp_path, dashboard_user="admin", dashboard_password="pw-1", dashboard_viewer_user="client", dashboard_viewer_password="pw-2")
    c = TestClient(create_app(s))
    viewer = {"Authorization": "Basic " + base64.b64encode(b"client:pw-2").decode()}
    admin = {"Authorization": "Basic " + base64.b64encode(b"admin:pw-1").decode()}
    assert c.get("/", headers=viewer).status_code == 200
    st = c.get("/api/state", headers=viewer)
    assert st.status_code == 200 and st.json()["role"] == "viewer"
    assert c.post("/api/control/flatten", headers=viewer).status_code == 403
    assert not s.flatten_file.exists()
    assert c.get("/api/state", headers=admin).json()["role"] == "admin"
    assert c.post("/api/control/stop", headers=admin).status_code == 200
