from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

@dataclass(frozen=True)
class Limits:
    max_position_fraction: float = 0.25
    max_exposure_fraction: float = 0.60
    daily_loss_fraction: float = 0.02
    total_loss_fraction: float = 0.08
    fee_rate: float = 0.006
    slippage_rate: float = 0.001

@dataclass
class Portfolio:
    cash: float = 1000.0
    initial: float = 1000.0
    holdings: dict[str, float] = field(default_factory=dict)
    last_prices: dict[str, float] = field(default_factory=dict)
    day_start_equity: float = 1000.0
    day: str = ""
    halted: bool = False

    def equity(self) -> float:
        return self.cash + sum(q * self.last_prices.get(symbol, 0) for symbol, q in self.holdings.items())

    def exposure(self) -> float:
        return sum(q * self.last_prices.get(symbol, 0) for symbol, q in self.holdings.items())

    def check_limits(self, limits: Limits, now: datetime) -> None:
        day = now.astimezone(timezone.utc).date().isoformat()
        if day != self.day:
            self.day = day
            self.day_start_equity = self.equity()
        equity = self.equity()
        if equity <= self.initial * (1 - limits.total_loss_fraction):
            self.halted = True
        if equity <= self.day_start_equity * (1 - limits.daily_loss_fraction):
            self.halted = True


def signal(closes: Sequence[float]) -> str:
    # Demonstration baseline, not a trained AI model.
    if len(closes) < 50 or any(x <= 0 for x in closes):
        return "HOLD"
    short = sum(closes[-10:]) / 10
    long = sum(closes[-50:]) / 50
    momentum = closes[-1] / closes[-6] - 1
    if short > long * 1.002 and momentum > 0.003:
        return "BUY"
    if short < long * 0.998 or momentum < -0.008:
        return "SELL"
    return "HOLD"


def execute_paper(portfolio: Portfolio, symbol: str, action: str, mid: float, limits: Limits, now: datetime):
    if mid <= 0:
        raise ValueError("Invalid price")
    portfolio.last_prices[symbol] = mid
    portfolio.check_limits(limits, now)
    if portfolio.halted or action == "HOLD":
        return None
    if action == "BUY":
        if portfolio.holdings.get(symbol, 0) > 0:
            return None
        max_position = portfolio.initial * limits.max_position_fraction
        remaining = max(0.0, portfolio.equity() * limits.max_exposure_fraction - portfolio.exposure())
        budget = min(max_position, remaining, portfolio.cash)
        if budget < 20:
            return None
        price = mid * (1 + limits.slippage_rate)
        qty = budget / (price * (1 + limits.fee_rate))
        cost = qty * price * (1 + limits.fee_rate)
        portfolio.cash -= cost
        portfolio.holdings[symbol] = portfolio.holdings.get(symbol, 0) + qty
        return (action, symbol, qty, price, qty * price * limits.fee_rate)
    if action == "SELL":
        qty = portfolio.holdings.get(symbol, 0)
        if qty <= 0:
            return None
        price = mid * (1 - limits.slippage_rate)
        fee = qty * price * limits.fee_rate
        portfolio.cash += qty * price - fee
        portfolio.holdings[symbol] = 0.0
        return (action, symbol, qty, price, fee)
    raise ValueError("Invalid action")
