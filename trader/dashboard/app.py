"""Read-mostly dashboard plus the emergency controls. Serves the SQLite data the trader writes."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import Settings
from ..gate import gate_status
from ..models import utcnow
from ..store import Store

TEMPLATE = Path(__file__).with_name("index.html")


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Coinbase AI Trader", docs_url=None, redoc_url=None)
    app.state.settings = settings
    store = Store(settings.database)

    def auth(request: Request) -> None:
        token = settings.dashboard_token
        if not token:
            return
        given = request.headers.get("x-token") or request.query_params.get("token") or request.cookies.get("token")
        if given != token:
            raise HTTPException(status_code=401, detail="missing or wrong token")

    def control_allowed() -> None:
        if not settings.dashboard_token:
            raise HTTPException(status_code=403, detail="set DASHBOARD_TOKEN to enable controls")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return TEMPLATE.read_text(encoding="utf-8")

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state() -> dict[str, Any]:
        st = store.load_state()
        now = utcnow()
        last_cycle = store.get_meta("last_cycle_at")
        stale = True
        if last_cycle:
            stale = now - datetime.fromisoformat(last_cycle) > timedelta(seconds=3 * settings.poll_seconds)
        gate_ok, gate_msg = gate_status(settings, now)
        d: dict[str, Any] = {
            "now": now.isoformat(timespec="seconds"),
            "mode": store.get_meta("mode", "paper"),
            "strategy": store.get_meta("strategy", settings.strategy),
            "profile": store.get_meta("risk_profile", settings.risk_profile),
            "products": settings.products,
            "quote_currency": settings.quote_currency,
            "report_currency": settings.report_currency,
            "starting_capital_report": settings.starting_capital_report,
            "started_at": store.get_meta("started_at"),
            "health": {
                "last_cycle_at": last_cycle, "stale": stale,
                "consecutive_errors": int(store.get_meta("consecutive_errors", "0") or 0),
                "manual_stop": settings.stop_file.exists(), "flatten_pending": settings.flatten_file.exists(),
                "live_trading_env": settings.live_trading, "live_armed": settings.live_armed_file.exists(),
                "gate_passed": gate_ok, "gate_message": gate_msg,
            },
            "limits": settings.profile.amounts(settings.starting_capital_report),
            "granularity": settings.granularity,
            "poll_seconds": settings.poll_seconds,
            "last_signals": json.loads(store.get_meta("last_signals") or "{}"),
            "state": st.to_dict() if st else None,
        }
        if st and st.fx_rate:
            d["report"] = {
                "fx_rate": st.fx_rate, "fx_rate_ts": st.fx_rate_ts,
                "equity": st.equity() / st.fx_rate, "cash": st.cash / st.fx_rate,
                "pnl": (st.equity() - st.initial_capital) / st.fx_rate,
                "pnl_pct": (st.equity() / st.initial_capital - 1) if st.initial_capital else 0.0,
                "note": f"converted at the {settings.report_currency}->{settings.quote_currency} rate recorded at start; see fx_rates for daily rates",
            }
        return d

    @app.get("/api/equity", dependencies=[Depends(auth)])
    def equity(limit: int = 2000) -> list[dict[str, Any]]:
        return store.snapshots(limit=min(limit, 20000))

    @app.get("/api/orders", dependencies=[Depends(auth)])
    def orders(limit: int = 100) -> list[dict[str, Any]]:
        return store.orders(limit=min(limit, 1000))

    @app.get("/api/events", dependencies=[Depends(auth)])
    def events(limit: int = 200, kind: str | None = None) -> list[dict[str, Any]]:
        return store.events(limit=min(limit, 2000), kind=kind)

    @app.get("/api/gate", dependencies=[Depends(auth)])
    def gate() -> dict[str, Any]:
        if settings.gate_file.exists():
            return json.loads(settings.gate_file.read_text())
        return {"passed": False, "checks": [], "message": "gate has not been run"}

    @app.post("/api/control/{action}", dependencies=[Depends(auth), Depends(control_allowed)])
    def control(action: str) -> dict[str, Any]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        ts = utcnow().isoformat(timespec="seconds")
        if action == "stop":
            settings.stop_file.write_text(f"stopped via dashboard {ts}\n")
            store.log(ts, "control", "STOP set via dashboard")
        elif action == "resume":
            settings.stop_file.unlink(missing_ok=True)
            store.log(ts, "control", "STOP cleared via dashboard")
        elif action == "flatten":
            settings.flatten_file.write_text(f"flatten via dashboard {ts}\n")
            store.log(ts, "control", "FLATTEN requested via dashboard")
        elif action == "disarm":
            settings.live_armed_file.unlink(missing_ok=True)
            settings.stop_file.write_text(f"disarmed via dashboard {ts}\n")
            store.log(ts, "control", "LIVE disarmed + STOP via dashboard")
        else:
            raise HTTPException(status_code=404, detail="unknown action")
        return {"ok": True, "action": action, "manual_stop": settings.stop_file.exists(),
                "flatten_pending": settings.flatten_file.exists(), "live_armed": settings.live_armed_file.exists()}

    @app.exception_handler(Exception)
    async def on_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"error": repr(exc)})

    return app
