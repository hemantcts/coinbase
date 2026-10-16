"""Plain data structures shared by every module."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class OrderStatus(str, Enum):
    NEW = "NEW"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Candle:
    ts: int          # unix seconds, candle open time, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def time(self) -> datetime:
        return datetime.fromtimestamp(self.ts, tz=timezone.utc)


@dataclass(frozen=True)
class Signal:
    product: str
    side: Side
    strategy: str
    confidence: float = 0.0       # 0..1, how strong the setup is
    expected_move: float = 0.0    # strategy's estimate of the favourable move, as a fraction
    reason: str = ""
    atr: float = 0.0              # current ATR in price units, for volatility-based stops

    @staticmethod
    def hold(product: str, strategy: str, reason: str = "") -> "Signal":
        return Signal(product, Side.HOLD, strategy, 0.0, 0.0, reason)


@dataclass
class Position:
    product: str
    qty: float
    entry_price: float
    entry_ts: str                 # ISO 8601
    strategy: str
    stop_price: float
    take_profit_price: float
    entry_fee: float = 0.0
    trail_atr_k: float = 0.0      # >0: stop trails at close - k*ATR
    atr_window: int = 14

    def value(self, price: float) -> float:
        return self.qty * price

    def unrealised(self, price: float) -> float:
        return self.qty * (price - self.entry_price) - self.entry_fee

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OrderIntent:
    """What the strategy would like to do. Goes to the risk controller first."""

    product: str
    side: Side
    price: float                  # reference price (last close / mid)
    signal: Signal
    reason: str = ""
    qty: float | None = None      # for sells: how much to close (None = all)
    stop_mode: str = "pct"        # copied from the strategy: "pct" or "atr"


@dataclass
class Decision:
    approved: bool
    reason: str
    qty: float = 0.0
    notional: float = 0.0
    stop_price: float = 0.0
    take_profit_price: float = 0.0


@dataclass
class Order:
    client_order_id: str
    product: str
    side: Side
    qty: float
    price: float                  # limit / reference price
    status: OrderStatus = OrderStatus.NEW
    created_at: str = ""
    updated_at: str = ""
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    fee: float = 0.0
    strategy: str = ""
    reason: str = ""
    mode: str = "paper"
    exchange_order_id: str = ""
    stop_price: float = 0.0
    take_profit_price: float = 0.0

    @property
    def is_done(self) -> bool:
        return self.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        d["status"] = self.status.value
        return d


@dataclass
class PortfolioState:
    """Everything needed to resume after a restart."""

    cash: float
    initial_capital: float                       # in quote currency
    positions: dict[str, Position] = field(default_factory=dict)
    last_prices: dict[str, float] = field(default_factory=dict)
    day: str = ""
    day_start_equity: float = 0.0
    halted_daily: bool = False
    halted_total: bool = False
    new_positions_today: int = 0
    realised_pnl: float = 0.0
    fees_paid: float = 0.0
    fx_rate: float | None = None                 # report -> quote (e.g. CHF->EUR) at start
    fx_rate_ts: str = ""

    def exposure(self) -> float:
        return sum(p.value(self.last_prices.get(sym, p.entry_price)) for sym, p in self.positions.items())

    def equity(self) -> float:
        return self.cash + self.exposure()

    def to_dict(self) -> dict[str, Any]:
        return {
            "cash": self.cash,
            "initial_capital": self.initial_capital,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "last_prices": dict(self.last_prices),
            "day": self.day,
            "day_start_equity": self.day_start_equity,
            "halted_daily": self.halted_daily,
            "halted_total": self.halted_total,
            "new_positions_today": self.new_positions_today,
            "realised_pnl": self.realised_pnl,
            "fees_paid": self.fees_paid,
            "fx_rate": self.fx_rate,
            "fx_rate_ts": self.fx_rate_ts,
            "equity": self.equity(),
            "exposure": self.exposure(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PortfolioState":
        positions = {k: Position(**v) for k, v in (d.get("positions") or {}).items()}
        return cls(
            cash=float(d["cash"]),
            initial_capital=float(d["initial_capital"]),
            positions=positions,
            last_prices={k: float(v) for k, v in (d.get("last_prices") or {}).items()},
            day=d.get("day", ""),
            day_start_equity=float(d.get("day_start_equity", 0.0)),
            halted_daily=bool(d.get("halted_daily", False)),
            halted_total=bool(d.get("halted_total", False)),
            new_positions_today=int(d.get("new_positions_today", 0)),
            realised_pnl=float(d.get("realised_pnl", 0.0)),
            fees_paid=float(d.get("fees_paid", 0.0)),
            fx_rate=d.get("fx_rate"),
            fx_rate_ts=d.get("fx_rate_ts", ""),
        )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
