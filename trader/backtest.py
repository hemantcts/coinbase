"""Backtesting on cached candles: same engine, same risk controller, no network.

Reports net return after fees and slippage, max drawdown, trade statistics and
fee drag, and compares strategies and risk profiles. Walk-forward splits the
data into an in-sample and an out-of-sample period so a strategy that merely
fits the past shows up.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import PROFILES, Costs, RiskProfile, Settings
from .data import StoreDataSource
from .engine import Engine
from .execution.paper import PaperBroker
from .risk import ProductSpec, RiskController
from .store import Store
from .strategies import build


@dataclass
class BacktestResult:
    strategy: str
    profile: str
    products: list[str]
    start: str
    end: str
    days: float
    cycles: int
    initial: float
    final: float
    net_return: float               # fraction
    benchmark_return: float         # buy & hold at the profile's max exposure, split equally across products
    max_drawdown: float             # fraction, peak to trough
    max_loss_from_start: float      # fraction, lowest equity vs starting capital (what the total-loss halt watches)
    round_trips: int
    buys: int
    sells: int
    win_rate: float
    profit_factor: float
    gross_profit: float
    gross_loss: float
    fees: float
    fee_drag: float                 # fees / initial capital
    avg_trade_pnl: float
    sharpe: float                   # annualised, from daily returns
    exposure_pct: float             # share of cycles with an open position
    halted_total: bool
    daily_halts: int               # all halt events (daily + total)
    stop_outs: int
    take_profits: int
    equity_curve: list[tuple[str, float]] = field(default_factory=list)

    def to_dict(self, with_curve: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not with_curve:
            d.pop("equity_curve")
        return d

    def summary_row(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy, "profile": self.profile, "days": round(self.days, 1),
            "net_%": round(100 * self.net_return, 2), "b&h_%": round(100 * self.benchmark_return, 2),
            "maxDD_%": round(100 * self.max_drawdown, 2), "worst_%": round(-100 * self.max_loss_from_start, 2),
            "trades": self.round_trips, "win_%": round(100 * self.win_rate, 1), "PF": round(self.profit_factor, 2),
            "fees": round(self.fees, 2), "fee_drag_%": round(100 * self.fee_drag, 2), "sharpe": round(self.sharpe, 2),
            "exposure_%": round(100 * self.exposure_pct, 1), "stops": self.stop_outs, "tps": self.take_profits,
            "halted": self.halted_total,
        }


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def run_backtest(candle_store: Store, products: list[str], strategy: str, profile: str | RiskProfile,
                 start: datetime, end: datetime, capital: float = 1000.0, costs: Costs | None = None,
                 granularity: int = 300, history: int = 300, specs: dict[str, ProductSpec] | None = None,
                 verbose: bool = False, halt_reset_days: int = 0) -> BacktestResult:
    costs = costs or Costs()
    prof = PROFILES[profile] if isinstance(profile, str) else profile
    settings = Settings(products=list(products), granularity=granularity, history_candles=history, costs=costs,
                        starting_capital_report=capital, quote_currency="EUR", report_currency="EUR")
    result_store = Store(":memory:")
    data = StoreDataSource(candle_store, granularity, specs)
    risk = RiskController(prof, costs, specs)
    risk.halt_reset_days = halt_reset_days
    engine = Engine(settings, result_store, PaperBroker(costs), build(strategy), risk,
                    data, fx=None, mode="backtest", verbose=verbose)

    # Timeline: every candle close inside [start, end] across all products.
    closes: set[int] = set()
    for p in products:
        for c in candle_store.candles(p, granularity, _ts(start), _ts(end)):
            closes.add(c.ts + granularity)
    if not closes:
        raise ValueError(f"no cached candles for {products} between {start:%Y-%m-%d} and {end:%Y-%m-%d}; run fetch-history first")
    timeline = sorted(closes)

    engine.init_state(datetime.fromtimestamp(timeline[0], tz=timezone.utc))
    curve: list[tuple[int, float]] = []
    exposed = 0
    state = engine.state
    assert state is not None
    for t in timeline:
        now = datetime.fromtimestamp(t, tz=timezone.utc)
        try:
            engine.run_cycle(now)
        except RuntimeError as exc:
            if "no market data" in str(exc):
                continue
            raise
        curve.append((t, state.equity()))
        if state.positions:
            exposed += 1

    fills = result_store.fills(limit=100000)
    benchmark = 0.0
    for p in products:
        rows = candle_store.candles(p, granularity, timeline[0] - granularity, timeline[-1])
        if len(rows) > 1 and rows[0].close > 0:
            benchmark += (prof.max_exposure_fraction / len(products)) * (rows[-1].close / rows[0].close - 1)
    return _metrics(engine, result_store, fills, curve, exposed, strategy, prof.name, products, timeline, capital, benchmark)


def _metrics(engine: Engine, store: Store, fills: list[dict[str, Any]], curve: list[tuple[int, float]], exposed: int,
             strategy: str, profile: str, products: list[str], timeline: list[int], capital: float,
             benchmark: float = 0.0) -> BacktestResult:
    state = engine.state
    assert state is not None
    final = state.equity()
    peak = capital
    max_dd = 0.0
    lowest = capital
    for _, eq in curve:
        peak = max(peak, eq)
        lowest = min(lowest, eq)
        if peak > 0:
            max_dd = max(max_dd, 1 - eq / peak)
    sells = [f for f in fills if f["side"] == "SELL"]
    buys = [f for f in fills if f["side"] == "BUY"]
    pnls = [f["pnl"] or 0.0 for f in sells]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    pf = gross_profit / gross_loss if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0)
    fees = sum(f["fee"] for f in fills)
    days = (timeline[-1] - timeline[0]) / 86400 if len(timeline) > 1 else 0.0

    # Daily returns for a Sharpe-like ratio.
    daily: dict[str, float] = {}
    for t, eq in curve:
        daily[datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()] = eq
    eqs = list(daily.values())
    rets = [eqs[i] / eqs[i - 1] - 1 for i in range(1, len(eqs)) if eqs[i - 1] > 0]
    sharpe = 0.0
    if len(rets) > 2:
        m = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))
        sharpe = (m / sd) * math.sqrt(365) if sd > 0 else 0.0

    stop_outs = sum(1 for f in sells if "stop-loss" in (f["reason"] or ""))
    take_profits = sum(1 for f in sells if "take-profit" in (f["reason"] or ""))
    return BacktestResult(
        strategy=strategy, profile=profile, products=list(products),
        start=datetime.fromtimestamp(timeline[0], tz=timezone.utc).isoformat(timespec="minutes"),
        end=datetime.fromtimestamp(timeline[-1], tz=timezone.utc).isoformat(timespec="minutes"),
        days=days, cycles=len(curve), initial=capital, final=final, net_return=final / capital - 1 if capital else 0.0,
        benchmark_return=benchmark,
        max_drawdown=max_dd, max_loss_from_start=(1 - lowest / capital) if capital else 0.0, round_trips=len(sells), buys=len(buys), sells=len(sells),
        win_rate=len(wins) / len(pnls) if pnls else 0.0, profit_factor=pf if pf != math.inf else 99.0,
        gross_profit=gross_profit, gross_loss=gross_loss, fees=fees, fee_drag=fees / capital if capital else 0.0,
        avg_trade_pnl=sum(pnls) / len(pnls) if pnls else 0.0, sharpe=sharpe,
        exposure_pct=exposed / len(curve) if curve else 0.0, halted_total=state.halted_total,
        daily_halts=store.count_events("halt"), stop_outs=stop_outs, take_profits=take_profits,
        equity_curve=[(datetime.fromtimestamp(t, tz=timezone.utc).isoformat(timespec="minutes"), round(e, 4)) for t, e in curve],
    )


def compare(candle_store: Store, products: list[str], strategies: list[str], profiles: list[str],
            start: datetime, end: datetime, **kw: Any) -> list[BacktestResult]:
    out = []
    for s in strategies:
        for p in profiles:
            out.append(run_backtest(candle_store, products, s, p, start, end, **kw))
    return out


def walk_forward(candle_store: Store, products: list[str], strategy: str, profile: str, start: datetime, end: datetime,
                 in_sample_fraction: float = 0.6, **kw: Any) -> tuple[BacktestResult, BacktestResult]:
    """Split the period: the first part is 'in-sample', the rest 'out-of-sample'."""
    split = datetime.fromtimestamp(start.timestamp() + (end.timestamp() - start.timestamp()) * in_sample_fraction, tz=timezone.utc)
    ins = run_backtest(candle_store, products, strategy, profile, start, split, **kw)
    oos = run_backtest(candle_store, products, strategy, profile, split, end, **kw)
    return ins, oos


def format_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(no results)"
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    line = "  ".join(c.rjust(widths[c]) for c in cols)
    sep = "  ".join("-" * widths[c] for c in cols)
    body = "\n".join("  ".join(str(r[c]).rjust(widths[c]) for c in cols) for r in rows)
    return f"{line}\n{sep}\n{body}"
