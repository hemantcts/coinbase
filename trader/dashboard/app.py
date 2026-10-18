"""Read-mostly dashboard plus the emergency controls. Serves the SQLite data the trader writes."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import parse_qs
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from ..config import Settings
from ..gate import gate_status
from ..models import utcnow
from ..store import Store

TEMPLATE = Path(__file__).with_name("index.html")
LOGIN_TEMPLATE = Path(__file__).with_name("login.html")
SESSION_HOURS = 12
COOKIE = "ta_session"


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Trading Agent", docs_url=None, redoc_url=None)
    app.state.settings = settings
    store = Store(settings.database)

    basic_enabled = bool(settings.dashboard_user and settings.dashboard_password)

    viewer_enabled = bool(settings.dashboard_viewer_user and settings.dashboard_viewer_password)

    def _basic_role(request: Request) -> str:
        """'admin', 'viewer' or '' for the credentials on the request."""
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return ""
        try:
            user, _, pw = base64.b64decode(header[6:].strip()).decode("utf-8").partition(":")
        except Exception:
            return ""
        if secrets.compare_digest(user.encode(), settings.dashboard_user.encode()) and \
           secrets.compare_digest(pw.encode(), settings.dashboard_password.encode()):
            return "admin"
        if viewer_enabled and secrets.compare_digest(user.encode(), settings.dashboard_viewer_user.encode()) and \
           secrets.compare_digest(pw.encode(), settings.dashboard_viewer_password.encode()):
            return "viewer"
        return ""

    def _basic_ok(request: Request) -> bool:
        return _basic_role(request) != ""

    # ---- session cookies: signed with a key derived from the secrets, so a rotation logs everyone out ----
    def _session_key() -> bytes:
        material = "|".join([settings.dashboard_password, settings.dashboard_viewer_password, settings.dashboard_token, "session-v1"])
        return hashlib.sha256(material.encode()).digest()

    def _make_session(role: str, user: str) -> str:
        payload = base64.urlsafe_b64encode(json.dumps({"r": role, "u": user, "exp": int(time.time()) + SESSION_HOURS * 3600}).encode()).decode().rstrip("=")
        sig = hmac.new(_session_key(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{sig}"

    def _session_role(request: Request) -> str:
        raw = request.cookies.get(COOKIE, "")
        if "." not in raw:
            return ""
        payload, _, sig = raw.rpartition(".")
        if not hmac.compare_digest(hmac.new(_session_key(), payload.encode(), hashlib.sha256).hexdigest(), sig):
            return ""
        try:
            data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except Exception:
            return ""
        if data.get("exp", 0) < time.time():
            return ""
        return data.get("r", "") if data.get("r") in ("admin", "viewer") else ""

    def _check_login(user: str, pw: str) -> str:
        if secrets.compare_digest(user.encode(), settings.dashboard_user.encode()) and \
           secrets.compare_digest(pw.encode(), settings.dashboard_password.encode()):
            return "admin"
        if viewer_enabled and secrets.compare_digest(user.encode(), settings.dashboard_viewer_user.encode()) and \
           secrets.compare_digest(pw.encode(), settings.dashboard_viewer_password.encode()):
            return "viewer"
        return ""

    failures: dict[str, list[float]] = {}

    def _locked(ip: str) -> bool:
        now_t = time.time()
        failures[ip] = [t for t in failures.get(ip, []) if now_t - t < 900]
        return len(failures[ip]) >= 10

    def _role(request: Request) -> str:
        return _session_role(request) or _basic_role(request) or ("admin" if _token_ok(request) else "")

    def _token_ok(request: Request) -> bool:
        token = settings.dashboard_token
        if not token:
            return False
        given = request.headers.get("x-token") or request.query_params.get("token") or request.cookies.get("token") or ""
        return secrets.compare_digest(given.encode(), token.encode())

    def page_auth(request: Request) -> None:
        """The page: a valid session cookie, else the login page."""
        if not basic_enabled or _role(request):
            return
        raise HTTPException(status_code=302, headers={"Location": "/login"})

    def auth(request: Request) -> None:
        """Data and controls: session cookie, basic auth or API token."""
        if basic_enabled:
            if _role(request):
                return
            time.sleep(0.8)
            raise HTTPException(status_code=401, detail="authentication required")
        if not settings.dashboard_token:
            return
        if not _token_ok(request):
            raise HTTPException(status_code=401, detail="missing or wrong token")

    def _login_page(error: str = "", next_url: str = "/") -> HTMLResponse:
        html = LOGIN_TEMPLATE.read_text(encoding="utf-8").replace("__ERROR__", error).replace("__NEXT__", next_url if next_url.startswith("/") else "/")
        return HTMLResponse(html, status_code=401 if error else 200, headers={"Cache-Control": "no-store"})

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request) -> HTMLResponse:
        if not basic_enabled or _role(request):
            return RedirectResponse("/", status_code=302)
        return _login_page()

    @app.post("/login")
    async def login_submit(request: Request):
        if not basic_enabled:
            return RedirectResponse("/", status_code=302)
        body = parse_qs((await request.body())[:4096].decode("utf-8", "replace"), keep_blank_values=True)
        username = body.get("username", [""])[0]
        password = body.get("password", [""])[0]
        next = body.get("next", ["/"])[0] or "/"
        ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
        if _locked(ip):
            time.sleep(1.0)
            return _login_page("locked", next)
        role = _check_login(username.strip(), password)
        if not role:
            failures.setdefault(ip, []).append(time.time())
            time.sleep(0.8)
            return _login_page("bad", next)
        failures.pop(ip, None)
        store.log(utcnow().isoformat(timespec="seconds"), "login", f"{role} login: {username.strip()}")
        resp = RedirectResponse(next if next.startswith("/") else "/", status_code=302)
        resp.set_cookie(COOKIE, _make_session(role, username.strip()), max_age=SESSION_HOURS * 3600, httponly=True,
                        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
                        samesite="strict", path="/")
        return resp

    @app.post("/logout")
    @app.get("/logout")
    def logout() -> RedirectResponse:
        resp = RedirectResponse("/login", status_code=302)
        resp.delete_cookie(COOKIE, path="/")
        return resp

    def control_allowed(request: Request) -> None:
        if not settings.dashboard_token and not basic_enabled:
            raise HTTPException(status_code=403, detail="set DASHBOARD_USER/DASHBOARD_PASSWORD or DASHBOARD_TOKEN to enable controls")
        if basic_enabled and _role(request) != "admin":
            raise HTTPException(status_code=403, detail="view-only login: controls are not available")

    @app.middleware("http")
    async def private_headers(request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots() -> str:
        return "User-agent: *\nDisallow: /\n"

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(page_auth)])
    def index() -> HTMLResponse:
        # Never let browsers (phones especially) keep a stale copy of the page after a deploy.
        return HTMLResponse(TEMPLATE.read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"})

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state(request: Request) -> dict[str, Any]:
        st = store.load_state()
        now = utcnow()
        last_cycle = store.get_meta("last_cycle_at")
        stale = True
        if last_cycle:
            stale = now - datetime.fromisoformat(last_cycle) > timedelta(seconds=3 * settings.poll_seconds)
        gate_ok, gate_msg = gate_status(settings, now)
        snaps = store.snapshots(limit=20000)
        peak, max_dd, lowest = 0.0, 0.0, None
        for row in snaps:
            eq = row["equity"]
            peak = max(peak, eq)
            lowest = eq if lowest is None else min(lowest, eq)
            if peak > 0:
                max_dd = max(max_dd, 1 - eq / peak)
        data_age = int(store.get_meta("data_age_seconds", "-1") or -1)
        max_age = settings.max_data_age or 2 * settings.granularity
        d: dict[str, Any] = {
            "now": now.isoformat(timespec="seconds"),
            "auth_mode": "basic" if basic_enabled else ("token" if settings.dashboard_token else "open"),
            "role": _role(request) if basic_enabled else "admin",
            "mode": store.get_meta("mode", "paper"),
            "strategy": store.get_meta("strategy", settings.strategy),
            "profile": store.get_meta("risk_profile", settings.risk_profile),
            "products": settings.products,
            "quote_currency": settings.quote_currency,
            "report_currency": settings.report_currency,
            "starting_capital_report": settings.starting_capital_report,
            "started_at": store.get_meta("started_at"),
            "performance": {
                "net_return": (st.equity() / st.initial_capital - 1) if st and st.initial_capital else 0.0,
                "max_drawdown": max_dd,
                "lowest_equity": lowest,
                "cycles": len(snaps),
            },
            "data_quality": {
                "age_seconds": data_age, "max_age_seconds": max_age,
                "ok": 0 <= data_age <= max_age,
            },
            "health": {
                "last_cycle_at": last_cycle, "stale": stale,
                "consecutive_errors": int(store.get_meta("consecutive_errors", "0") or 0),
                "manual_stop": settings.stop_file.exists(), "flatten_pending": settings.flatten_file.exists(),
                "severe_event": store.get_meta("severe_event", "") or "",
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
            settings.flatten_file.unlink(missing_ok=True)
            store.log(ts, "control", "STOP / FLATTEN cleared via dashboard")
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
