"""Command line entry point: python -m trader <command>."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from . import __version__
from .backtest import compare, format_table, walk_forward
from .config import PROFILES, Settings, pilot_profile
from .data import CoinbasePublic
from .engine import Engine
from .execution.paper import PaperBroker
from .fx import FxService
from .gate import format_checks, gate_status, run_gate
from .notify import Notifier
from .risk import RiskController
from .store import Store
from .strategies import ALL, build


def _date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc) if "T" in s else datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _period(args: argparse.Namespace, default_days: int = 90) -> tuple[datetime, datetime]:
    end = _date(args.end) if getattr(args, "end", None) else datetime.now(timezone.utc)
    start = _date(args.start) if getattr(args, "start", None) else end - timedelta(days=getattr(args, "days", None) or default_days)
    return start, end


def cmd_profiles(settings: Settings, args: argparse.Namespace) -> int:
    cap = settings.starting_capital_report
    rows = []
    for name, p in PROFILES.items():
        a = p.amounts(cap)
        rows.append({"profile": name, **{k: round(v, 2) for k, v in a.items()},
                     "max_new/day": p.max_new_positions_per_day, "stop_%": p.stop_loss_pct * 100, "target_%": p.take_profit_pct * 100,
                     "edge_x_costs": p.min_edge_multiple})
    print(f"Risk profiles for {cap:.0f} {settings.report_currency} (round-trip cost assumed {settings.costs.round_trip:.2%}):")
    print(format_table(rows))
    pp = pilot_profile(settings.profile)
    print(f"\nLive pilot caps on '{settings.profile.name}': max position {pp.max_position_fraction*cap:.0f}, max invested {pp.max_exposure_fraction*cap:.0f} {settings.report_currency}")
    return 0


def cmd_fetch_history(settings: Settings, args: argparse.Namespace) -> int:
    store = Store(settings.database)
    public = CoinbasePublic(settings.granularity, store=store)
    for product in args.products or settings.products:
        spec = public.spec(product)
        print(f"{product}: min size {spec.base_min_size}, increment {spec.base_increment}, quote increment {spec.quote_increment}")
        n = public.fetch_history(product, args.days, store)
        a, b, cnt = store.candle_range(product, settings.granularity)
        print(f"  cached {cnt} candles ({datetime.fromtimestamp(a, tz=timezone.utc):%Y-%m-%d} .. {datetime.fromtimestamp(b, tz=timezone.utc):%Y-%m-%d}), {n} written")
    return 0


def cmd_backtest(settings: Settings, args: argparse.Namespace) -> int:
    store = Store(settings.database)
    start, end = _period(args)
    strategies = list(ALL) + ["ensemble"] if args.strategy == "all" else [args.strategy]
    profiles = list(PROFILES) if args.profile == "all" else [args.profile]
    kw = dict(capital=args.capital or settings.starting_capital_report, costs=settings.costs, granularity=settings.granularity)
    products = args.products or settings.products
    print(f"Backtest {products} {start:%Y-%m-%d} .. {end:%Y-%m-%d}, fees {settings.costs.fee_rate:.2%}, slippage {settings.costs.slippage_rate:.2%}")
    if args.walk_forward:
        rows, out = [], {}
        for s in strategies:
            for p in profiles:
                ins, oos = walk_forward(store, products, s, p, start, end, **kw)
                rows.append({"period": "in-sample", **ins.summary_row()})
                rows.append({"period": "out-of-sample", **oos.summary_row()})
                out[f"{s}/{p}"] = {"in_sample": ins.to_dict(), "out_of_sample": oos.to_dict(args.curves)}
        print(format_table(rows))
    else:
        results = compare(store, products, strategies, profiles, start, end, **kw)
        print(format_table([r.summary_row() for r in results]))
        out = {f"{r.strategy}/{r.profile}": r.to_dict(args.curves) for r in results}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        print(f"written {args.json}")
    print("\nResults are in the quote currency, after the assumed fees and slippage. Past results do not predict future returns.")
    return 0


def _make_engine(settings: Settings, mode: str) -> Engine:
    store = Store(settings.database)
    public = CoinbasePublic(settings.granularity, store=store)
    specs = {p: public.spec(p) for p in settings.products}
    fx = FxService(store, settings.fx_fallback_rate, settings.report_currency, settings.quote_currency)
    notifier = Notifier(settings.notify_webhook)
    if mode == "live":
        from .execution.live import CoinbaseAdvancedBroker, assert_armed
        assert_armed(settings)
        broker = CoinbaseAdvancedBroker(settings, settings.costs)
        perms = broker.permissions()
        if perms.get("can_transfer"):
            raise RuntimeError("API key has transfer permission; create a trade-only key without transfers")
        if not perms.get("can_trade"):
            raise RuntimeError("API key cannot trade")
        profile = pilot_profile(settings.profile)
        specs = {p: broker.spec(p) for p in settings.products}
    else:
        broker = PaperBroker(settings.costs)
        profile = settings.profile
    risk = RiskController(profile, settings.costs, specs)
    return Engine(settings, store, broker, build(settings.strategy), risk, public, fx, notifier, mode=mode)


def cmd_paper(settings: Settings, args: argparse.Namespace) -> int:
    if settings.live_trading:
        print("Refusing to run paper mode with LIVE_TRADING=true. Unset it.", file=sys.stderr)
        return 2
    engine = _make_engine(settings, "paper")
    print(f"Coinbase AI Trader {__version__} PAPER. No API keys are used. Capital {settings.starting_capital_report:.0f} {settings.report_currency}.")
    engine.run_forever()
    return 0


def cmd_live(settings: Settings, args: argparse.Namespace) -> int:
    engine = _make_engine(settings, "live")
    store_mode = engine.store.get_meta("mode")
    if store_mode and store_mode != "live":
        print(f"Database {settings.database} holds a {store_mode} account. Use a separate DB_PATH for live.", file=sys.stderr)
        return 2
    print("LIVE MODE ARMED. Pilot caps active. Press Ctrl-C or create data/STOP to halt new buys.")
    engine.run_forever()
    return 0


def cmd_verify_live(settings: Settings, args: argparse.Namespace) -> int:
    from .execution.live import CoinbaseAdvancedBroker
    if not settings.coinbase_api_key:
        print("COINBASE_API_KEY not set", file=sys.stderr)
        return 2
    broker = CoinbaseAdvancedBroker(settings, settings.costs)
    perms = broker.permissions()
    print("API key permissions:", json.dumps(perms))
    if perms.get("can_transfer"):
        print("FAIL: key can transfer funds. Create a new key WITHOUT transfer/withdrawal permission.")
        return 1
    bal = broker.balances()
    print("Balances:", json.dumps({k: v for k, v in bal.items() if v}, indent=2))
    for p in settings.products:
        print(p, broker.spec(p))
    ok, why = gate_status(settings)
    print("Release gate:", why)
    print("Live armed file present:", settings.live_armed_file.exists(), "| LIVE_TRADING env:", settings.live_trading)
    return 0


def cmd_gate(settings: Settings, args: argparse.Namespace) -> int:
    store = Store(settings.database)
    start, end = _period(args)
    result = run_gate(settings, store, store, start, end, min_paper_hours=args.min_paper_hours)
    print(f"Release gate for {settings.strategy}/{settings.profile.name} on {settings.products}")
    print("In-sample:     ", result["in_sample"])
    print("Out-of-sample: ", result["out_of_sample"])
    print(format_checks(result["checks"]))
    print(f"\nRESULT: {'PASS' if result['passed'] else 'FAIL'}  (written to {settings.gate_file})")
    return 0 if result["passed"] else 1


def cmd_status(settings: Settings, args: argparse.Namespace) -> int:
    store = Store(settings.database)
    st = store.load_state()
    if not st:
        print("no state yet")
        return 0
    d = st.to_dict()
    print(json.dumps({k: v for k, v in d.items() if k != "positions"}, indent=2))
    print("positions:", json.dumps(d["positions"], indent=2))
    print("last cycle:", store.get_meta("last_cycle_at"), "| errors in a row:", store.get_meta("consecutive_errors"))
    print("STOP:", settings.stop_file.exists(), "| FLATTEN:", settings.flatten_file.exists(), "| gate:", gate_status(settings)[1])
    return 0


def cmd_dashboard(settings: Settings, args: argparse.Namespace) -> int:
    import uvicorn
    from .dashboard import create_app
    if not settings.dashboard_token:
        print("WARNING: DASHBOARD_TOKEN not set; the dashboard is read-only and unauthenticated. Bind to localhost only.")
    uvicorn.run(create_app(settings), host=settings.dashboard_host, port=settings.dashboard_port, log_level="info")
    return 0


def cmd_control(settings: Settings, args: argparse.Namespace) -> int:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if args.action == "stop":
        settings.stop_file.write_text("stopped via cli\n")
    elif args.action == "resume":
        settings.stop_file.unlink(missing_ok=True)
    elif args.action == "flatten":
        settings.flatten_file.write_text("flatten via cli\n")
    elif args.action == "disarm":
        settings.live_armed_file.unlink(missing_ok=True)
        settings.stop_file.write_text("disarmed via cli\n")
    print(f"STOP={settings.stop_file.exists()} FLATTEN={settings.flatten_file.exists()} ARMED={settings.live_armed_file.exists()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="trader", description=f"Coinbase AI Trader {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("profiles", help="show the risk profiles in money terms")
    s = sub.add_parser("fetch-history", help="cache historical candles for backtests")
    s.add_argument("--days", type=int, default=120)
    s.add_argument("--products", nargs="*")
    s = sub.add_parser("backtest", help="simulate strategies on cached candles")
    s.add_argument("--strategy", default="all", choices=list(ALL) + ["ensemble", "all"])
    s.add_argument("--profile", default="medium", choices=list(PROFILES) + ["all"])
    s.add_argument("--start"); s.add_argument("--end"); s.add_argument("--days", type=int)
    s.add_argument("--capital", type=float)
    s.add_argument("--products", nargs="*")
    s.add_argument("--walk-forward", action="store_true")
    s.add_argument("--json"); s.add_argument("--curves", action="store_true", help="include equity curves in --json")
    s = sub.add_parser("gate", help="run the automatic release checks")
    s.add_argument("--start"); s.add_argument("--end"); s.add_argument("--days", type=int)
    s.add_argument("--min-paper-hours", type=float, default=72.0)
    sub.add_parser("paper", help="paper trade on live market data")
    sub.add_parser("live", help="trade real money (gated)")
    sub.add_parser("verify-live", help="read-only check of the Coinbase API key")
    sub.add_parser("status", help="print the current account state")
    sub.add_parser("dashboard", help="serve the web dashboard")
    s = sub.add_parser("control", help="emergency controls")
    s.add_argument("action", choices=["stop", "resume", "flatten", "disarm"])

    args = p.parse_args(argv)
    settings = Settings.from_env()
    handlers = {
        "profiles": cmd_profiles, "fetch-history": cmd_fetch_history, "backtest": cmd_backtest, "gate": cmd_gate,
        "paper": cmd_paper, "live": cmd_live, "verify-live": cmd_verify_live, "status": cmd_status,
        "dashboard": cmd_dashboard, "control": cmd_control,
    }
    try:
        return handlers[args.command](settings, args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
