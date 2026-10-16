"""Release gate: the automatic checks that must pass before real money is allowed.

Two halves, mirroring the project plan:
  Phase A  out-of-sample backtest of the configured strategy and profile
  Phase B  at least 72 hours of continuous paper trading without technical faults
The result is written to data/gate_result.json. The live broker refuses to
start unless that file says PASS and is younger than 7 days.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from .backtest import BacktestResult, walk_forward
from .config import RiskProfile, Settings
from .models import iso, utcnow
from .store import Store

GATE_MAX_AGE_DAYS = 7


@dataclass
class Check:
    phase: str
    name: str
    passed: bool
    detail: str


def backtest_checks(oos: BacktestResult, profile: RiskProfile, min_trades: int = 5) -> list[Check]:
    c = []
    beats = oos.net_return > 0 or oos.net_return > oos.benchmark_return
    c.append(Check("A", "oos_profit_or_beats_market", beats,
                   f"net {oos.net_return:+.2%} vs buy-and-hold {oos.benchmark_return:+.2%} over {oos.days:.0f} days "
                   "(a trend follower must make money or lose less than the market)"))
    c.append(Check("A", "oos_loss_within_limit", oos.max_loss_from_start < profile.total_loss_fraction,
                   f"worst loss from start {oos.max_loss_from_start:.2%} vs total loss limit {profile.total_loss_fraction:.0%} "
                   f"(peak-to-trough drawdown {oos.max_drawdown:.2%})"))
    c.append(Check("A", "oos_enough_trades", oos.round_trips >= min_trades, f"{oos.round_trips} round trips (min {min_trades})"))
    c.append(Check("A", "oos_not_halted", not oos.halted_total, "total loss halt " + ("triggered" if oos.halted_total else "not triggered")))
    fee_ok = oos.gross_profit <= 0 or oos.fees / oos.gross_profit < 0.5
    c.append(Check("A", "fees_below_half_of_gross_profit", fee_ok, f"fees {oos.fees:.2f} vs gross profit {oos.gross_profit:.2f}"))
    return c


def paper_checks(store: Store, settings: Settings, min_hours: float = 72.0, now: datetime | None = None,
                 max_gap_minutes: float = 30.0, max_error_ratio: float = 0.02) -> list[Check]:
    now = now or utcnow()
    c: list[Check] = []
    snaps = store.snapshots(limit=200000)
    if not snaps:
        return [Check("B", "paper_run_exists", False, "no snapshots: paper trading has not run")]
    first = datetime.fromisoformat(snaps[0]["ts"])
    last = datetime.fromisoformat(snaps[-1]["ts"])
    hours = (last - first).total_seconds() / 3600
    c.append(Check("B", "paper_duration", hours >= min_hours, f"{hours:.1f} h of paper trading (min {min_hours:.0f} h)"))

    gaps = []
    prev = first
    for s in snaps[1:]:
        t = datetime.fromisoformat(s["ts"])
        gaps.append((t - prev).total_seconds() / 60)
        prev = t
    worst = max(gaps) if gaps else 0.0
    c.append(Check("B", "paper_continuity", worst <= max_gap_minutes, f"largest gap between cycles {worst:.1f} min (max {max_gap_minutes:.0f})"))

    still_running = (now - last) <= timedelta(seconds=3 * settings.poll_seconds)
    c.append(Check("B", "paper_still_running", still_running, f"last cycle {iso(last)}"))

    errors = store.count_events("error")
    ratio = errors / max(1, len(snaps))
    c.append(Check("B", "paper_error_rate", ratio <= max_error_ratio, f"{errors} errors over {len(snaps)} cycles ({ratio:.2%})"))

    # Position duplication: fills per product must alternate BUY / SELL.
    fills = sorted(store.fills(limit=100000), key=lambda f: f["updated_at"])
    dup = 0
    holding: dict[str, bool] = {}
    for f in fills:
        if f["side"] == "BUY":
            if holding.get(f["product"]):
                dup += 1
            holding[f["product"]] = True
        else:
            holding[f["product"]] = False
    c.append(Check("B", "no_duplicate_positions", dup == 0, f"{dup} buys while already holding"))

    # Halts honoured: no BUY fill after a halt on the same UTC day.
    violations = 0
    for h in store.events(limit=10000, kind="halt"):
        day = h["ts"][:10]
        for f in fills:
            if f["side"] == "BUY" and f["updated_at"] > h["ts"] and f["updated_at"][:10] == day:
                violations += 1
    c.append(Check("B", "halts_honoured", violations == 0, f"{violations} buys after a halt on the same day"))

    fx_source = store.get_meta("fx_source_at_start", "none") or "none"
    c.append(Check("B", "fx_rate_recorded", fx_source not in ("none", ""), f"FX source at start: {fx_source}"))
    c.append(Check("B", "fx_rate_not_fallback", fx_source != "fallback", "fallback rate was used" if fx_source == "fallback" else "live rate"))
    c.append(Check("B", "paper_mode_only", (store.get_meta("mode") or "paper") != "live", f"mode {store.get_meta('mode')}"))
    return c


def run_gate(settings: Settings, candle_store: Store, paper_store: Store, start: datetime, end: datetime,
             min_paper_hours: float = 72.0, write: bool = True) -> dict[str, Any]:
    profile = settings.profile
    ins, oos = walk_forward(candle_store, settings.products, settings.strategy, profile.name, start, end,
                            capital=settings.starting_capital_report, costs=settings.costs, granularity=settings.granularity,
                            halt_reset_days=settings.halt_reset_days)
    checks = backtest_checks(oos, profile) + paper_checks(paper_store, settings, min_paper_hours)
    passed = all(c.passed for c in checks)
    result = {
        "ts": iso(utcnow()),
        "passed": passed,
        "strategy": settings.strategy,
        "profile": profile.name,
        "products": settings.products,
        "in_sample": ins.summary_row(),
        "out_of_sample": oos.summary_row(),
        "checks": [asdict(c) for c in checks],
        "valid_until": iso(utcnow() + timedelta(days=GATE_MAX_AGE_DAYS)),
    }
    if write:
        settings.gate_file.parent.mkdir(parents=True, exist_ok=True)
        settings.gate_file.write_text(json.dumps(result, indent=2))
    return result


def gate_status(settings: Settings, now: datetime | None = None) -> tuple[bool, str]:
    """Is there a passed, recent gate result on disk?"""
    now = now or utcnow()
    f = settings.gate_file
    if not f.exists():
        return False, "no gate result on disk; run `trader gate`"
    try:
        d = json.loads(f.read_text())
    except (OSError, ValueError) as exc:
        return False, f"gate result unreadable: {exc!r}"
    if not d.get("passed"):
        failed = [c["name"] for c in d.get("checks", []) if not c.get("passed")]
        return False, f"gate FAILED on {d.get('ts')}: {', '.join(failed)}"
    ts = datetime.fromisoformat(d["ts"])
    if now - ts > timedelta(days=GATE_MAX_AGE_DAYS):
        return False, f"gate result from {d['ts']} is older than {GATE_MAX_AGE_DAYS} days"
    if d.get("strategy") != settings.strategy or d.get("profile") != settings.profile.name:
        return False, f"gate was run for {d.get('strategy')}/{d.get('profile')}, configured {settings.strategy}/{settings.profile.name}"
    return True, f"gate passed on {d['ts']}"


def format_checks(checks: list[dict[str, Any]]) -> str:
    lines = []
    for c in checks:
        mark = "PASS" if c["passed"] else "FAIL"
        lines.append(f"  [{mark}] {c['phase']}  {c['name']:<32} {c['detail']}")
    return "\n".join(lines)
