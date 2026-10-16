"""Independent risk controller.

Every order intent passes through `review`. The controller can block any buy.
It never blocks an exit: a halt stops new risk, it does not trap the account
in a losing position. The strategies cannot change these limits.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Costs, RiskProfile
from .models import Candle, Decision, OrderIntent, PortfolioState, Position, Side, Signal


@dataclass(frozen=True)
class ProductSpec:
    """Exchange constraints for one product."""

    product: str
    base_increment: float = 1e-8
    base_min_size: float = 0.0
    quote_increment: float = 0.01
    min_market_funds: float = 0.0

    def round_qty(self, qty: float) -> float:
        if self.base_increment <= 0:
            return qty
        steps = math.floor(qty / self.base_increment + 1e-9)
        return round(steps * self.base_increment, 12)

    def round_price(self, price: float) -> float:
        if self.quote_increment <= 0:
            return price
        return round(round(price / self.quote_increment) * self.quote_increment, 10)


class RiskController:
    def __init__(self, profile: RiskProfile, costs: Costs, specs: dict[str, ProductSpec] | None = None):
        self.profile = profile
        self.costs = costs
        self.specs = specs or {}
        self.manual_stop = False       # set by the engine when the STOP file exists

    # ---- day roll and halts -------------------------------------------------

    def roll_day(self, state: PortfolioState, now: datetime) -> bool:
        """Start a new trading day (UTC). Returns True when the day changed."""
        day = now.astimezone(timezone.utc).date().isoformat()
        if day == state.day:
            return False
        state.day = day
        state.day_start_equity = state.equity()
        state.halted_daily = False
        state.new_positions_today = 0
        return True

    def update_halts(self, state: PortfolioState) -> list[str]:
        """Re-check loss limits on marked-to-market equity. Returns new halt reasons."""
        p = self.profile
        events: list[str] = []
        equity = state.equity()
        total_floor = state.initial_capital * (1 - p.total_loss_fraction)
        if not state.halted_total and equity <= total_floor:
            state.halted_total = True
            events.append(f"TOTAL LOSS LIMIT: equity {equity:.2f} <= {total_floor:.2f}")
        if state.day_start_equity > 0:
            daily_floor = state.day_start_equity * (1 - p.daily_loss_fraction)
            if not state.halted_daily and equity <= daily_floor:
                state.halted_daily = True
                events.append(f"DAILY LOSS LIMIT: equity {equity:.2f} <= {daily_floor:.2f}")
        return events

    # ---- order review --------------------------------------------------------

    def review(self, intent: OrderIntent, state: PortfolioState) -> Decision:
        if intent.side == Side.SELL:
            return self._review_sell(intent, state)
        if intent.side == Side.BUY:
            return self._review_buy(intent, state)
        return Decision(False, "nothing to do")

    def _review_sell(self, intent: OrderIntent, state: PortfolioState) -> Decision:
        pos = state.positions.get(intent.product)
        if pos is None or pos.qty <= 0:
            return Decision(False, "no position to sell")
        qty = pos.qty if intent.qty is None else min(intent.qty, pos.qty)
        spec = self.specs.get(intent.product)
        if spec:
            qty = spec.round_qty(qty)
            if qty <= 0:
                return Decision(False, "quantity rounds to zero")
        # Exits are always allowed, even when halted or manually stopped.
        return Decision(True, "exit approved", qty=qty, notional=qty * intent.price)

    def _review_buy(self, intent: OrderIntent, state: PortfolioState) -> Decision:
        p, c = self.profile, self.costs
        if intent.price <= 0:
            return Decision(False, "invalid price")
        if self.manual_stop:
            return Decision(False, "manual STOP active")
        if state.halted_total:
            return Decision(False, "total loss limit reached, trading halted")
        if state.halted_daily:
            return Decision(False, "daily loss limit reached, no new positions today")
        if intent.product in state.positions and state.positions[intent.product].qty > 0:
            return Decision(False, "already holding this product")
        if state.new_positions_today >= p.max_new_positions_per_day:
            return Decision(False, f"max {p.max_new_positions_per_day} new positions per day reached")

        # Cost-aware edge: the expected move must clearly beat the round trip.
        required = c.round_trip * p.min_edge_multiple
        if intent.signal.expected_move < required:
            return Decision(False, f"expected move {intent.signal.expected_move:.3%} < required {required:.3%} after costs")

        capital = state.initial_capital
        max_position = capital * p.max_position_fraction
        exposure_room = capital * p.max_exposure_fraction - state.exposure()
        reserve_room = state.cash - capital * p.min_reserve_fraction
        risk_room = capital * p.risk_per_trade_fraction / p.stop_loss_pct
        notional = min(max_position, exposure_room, reserve_room, risk_room, state.cash)
        if notional < p.min_order_notional:
            return Decision(False, f"budget {max(notional, 0):.2f} below minimum order {p.min_order_notional:.2f} "
                                   f"(position cap {max_position:.0f}, exposure room {exposure_room:.0f}, "
                                   f"reserve room {reserve_room:.0f}, risk room {risk_room:.0f})")

        fill_estimate = intent.price * (1 + c.slippage_rate)
        qty = notional / (fill_estimate * (1 + c.fee_rate))
        spec = self.specs.get(intent.product)
        if spec:
            qty = spec.round_qty(qty)
            if qty < spec.base_min_size or qty <= 0:
                return Decision(False, f"quantity {qty} below exchange minimum {spec.base_min_size}")
            if spec.min_market_funds and qty * intent.price < spec.min_market_funds:
                return Decision(False, f"notional below exchange minimum funds {spec.min_market_funds}")
        if self._atr_mode(intent) and intent.signal.atr > 0:
            stop = intent.price - p.atr_multiple * intent.signal.atr
            take = 0.0                                   # no target: the trend decides the exit
        else:
            stop = intent.price * (1 - p.stop_loss_pct)
            take = intent.price * (1 + p.take_profit_pct)
        if spec:
            stop, take = spec.round_price(stop), spec.round_price(take)
        return Decision(True, "approved", qty=qty, notional=qty * fill_estimate * (1 + c.fee_rate),
                        stop_price=max(stop, 0.0), take_profit_price=max(take, 0.0))

    @staticmethod
    def _atr_mode(intent: OrderIntent) -> bool:
        return getattr(intent, "stop_mode", "pct") == "atr"

    # ---- protective exits ----------------------------------------------------

    def protective_exit(self, position: Position, candle: Candle) -> OrderIntent | None:
        """Stop-loss / take-profit check against the latest closed candle. A zero level means 'none'."""
        if position.qty <= 0:
            return None
        if position.stop_price > 0 and candle.low <= position.stop_price:
            sig = Signal(position.product, Side.SELL, "risk", 1.0, 0.0, f"stop-loss {position.stop_price:.2f} hit (low {candle.low:.2f})")
            return OrderIntent(position.product, Side.SELL, position.stop_price, sig, sig.reason)
        if position.take_profit_price > 0 and candle.high >= position.take_profit_price:
            sig = Signal(position.product, Side.SELL, "risk", 1.0, 0.0, f"take-profit {position.take_profit_price:.2f} hit (high {candle.high:.2f})")
            return OrderIntent(position.product, Side.SELL, position.take_profit_price, sig, sig.reason)
        return None

    def trail_stop(self, position: Position, candles: list[Candle]) -> float | None:
        """Raise a trailing ATR stop after a closed candle. Never lowers it. Returns the new stop or None."""
        if position.qty <= 0 or position.trail_atr_k <= 0 or len(candles) < position.atr_window + 1:
            return None
        from . import indicators as ind
        atr = ind.atr([c.high for c in candles], [c.low for c in candles], [c.close for c in candles], position.atr_window)
        if not atr:
            return None
        candidate = candles[-1].close - position.trail_atr_k * atr
        if candidate > position.stop_price:
            position.stop_price = candidate
            return candidate
        return None
