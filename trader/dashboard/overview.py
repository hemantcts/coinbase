"""At-a-glance bot status for the dashboard: is it running, what is it doing, what did it last see.

Pure read of what the engine writes (state, meta, orders, events, data/universe.json); the engine is not asked.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..config import Settings
from ..models import PortfolioState
from ..store import Store

# Event kinds that mean the engine itself did something (logins and dashboard clicks do not count).
ENGINE_KINDS = ("fill", "signal", "rejected", "order_open", "order_rejected", "order_retry", "order_cancelled",
                "trail", "halt", "flatten", "start", "restart", "day_start", "universe", "stale_data", "severe_event")


def _detail(row: dict[str, Any] | None) -> Any:
    if not row:
        return None
    try:
        return json.loads(row["detail"])
    except (TypeError, ValueError):
        return row["detail"]


def bot_status(stale: bool, has_cycle: bool, st: PortfolioState | None, manual_stop: bool, flatten_pending: bool,
               severe_event: str, data_ok: bool) -> tuple[str, str]:
    """RUNNING, PAUSED (alive but new buys blocked) or STOPPED (no recent cycle). Returns (status, reason code)."""
    if not has_cycle:
        return "STOPPED", "never_ran"
    if stale:
        return "STOPPED", "no_recent_cycle"
    if flatten_pending:
        return "PAUSED", "flatten"
    if manual_stop:
        return "PAUSED", "manual_stop"
    if st and st.halted_total:
        return "PAUSED", "halt_total"
    if st and st.halted_daily:
        return "PAUSED", "halt_daily"
    if severe_event:
        return "PAUSED", "severe_event"
    if not data_ok:
        return "PAUSED", "stale_data"
    return "RUNNING", "ok"


def overview(store: Store, settings: Settings, st: PortfolioState | None, health: dict[str, Any], data_ok: bool,
             now: datetime) -> dict[str, Any]:
    status, why = bot_status(health["stale"], bool(health["last_cycle_at"]), st, health["manual_stop"],
                             health["flatten_pending"], health["severe_event"], data_ok)
    positions = st.positions if st else {}
    pending = store.open_orders()
    completed = store.db.execute("SELECT COUNT(*) FROM orders WHERE status='FILLED' AND side='SELL'").fetchone()[0]
    if status == "STOPPED":
        activity = "stopped"
    elif pending:
        activity = "executing"
    elif positions:
        activity = "in_trade"
    else:
        activity = "monitoring"

    traded = [p for p in (store.get_meta("products") or "").split(",") if p] or list(settings.products)
    watched = [p for p in (store.get_meta("watch_products") or "").split(",") if p and p not in traded]
    signals = json.loads(store.get_meta("last_signals") or "{}")
    ranking: dict[str, dict[str, Any]] = {}
    try:
        u = json.loads(settings.universe_file.read_text())
        ranking = {c["product"]: {"rank": i + 1, "score": c.get("score")} for i, c in enumerate(u.get("coins", []))}
        universe_ts = u.get("ts")
    except (OSError, ValueError, KeyError, TypeError):
        universe_ts = None
    coins = []
    for p in traded + watched:
        g = signals.get(p) or {}
        pos = positions.get(p)
        price = (st.last_prices.get(p) if st and p in traded else None) or g.get("price")
        row: dict[str, Any] = {
            "product": p, "traded": p in traded, "rank": ranking.get(p, {}).get("rank"), "score": ranking.get(p, {}).get("score"),
            "price": price, "side": g.get("side"), "play": pos.strategy if pos else g.get("strategy"),
            "reason": g.get("reason", ""), "signal_ts": g.get("ts"), "in_trade": pos is not None,
        }
        if pos and price:
            row["pnl"] = pos.unrealised(price)
            row["pnl_pct"] = price / pos.entry_price - 1 if pos.entry_price else None
        coins.append(row)
    coins.sort(key=lambda c: (not c["in_trade"], not c["traded"], c["rank"] or 99))

    last_signal = store.events(limit=1, kind="signal")
    last_fill = store.events(limit=1, kind="fill")
    marks = ",".join("?" * len(ENGINE_KINDS))
    row = store.db.execute(f"SELECT ts FROM events WHERE kind IN ({marks}) ORDER BY id DESC LIMIT 1", ENGINE_KINDS).fetchone()
    last_activity = max(filter(None, [health["last_cycle_at"], row[0] if row else None]), default=None)
    plays = sorted({p.strategy for p in positions.values()})
    return {
        "status": status, "status_reason": why, "activity": activity,
        "counts": {"completed": completed, "active": len(positions), "pending": len(pending),
                   "markets": len(traded), "watched": len(watched)},
        "active_plays": plays,
        "coins": coins, "universe_ts": universe_ts,
        "last_signal": ({"ts": last_signal[0]["ts"], **(_detail(last_signal[0]) or {})} if last_signal else None),
        "last_fill": ({"ts": last_fill[0]["ts"], **(_detail(last_fill[0]) or {})} if last_fill else None),
        "last_activity_at": last_activity,
        "server_time": now.isoformat(timespec="seconds"),
    }
