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
from .models import Candle, Decision, Evidence, OrderIntent, PortfolioState, Position, Side, Signal


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
        self.stale_data = False        # set by the engine when market data is too old; blocks new buys
        self.severe_event = ""         # set by apply_evidence(); non-empty blocks new buys
        self.severe_event_threshold = 0.7
        self.max_evidence_age_hours = 24
        # Research only: days after which a total-loss halt clears itself. 0 = never (live behaviour:
        # the owner reviews the cause and restarts by hand).
        self.halt_reset_days = 0

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
        if state.halted_total and self.halt_reset_days > 0 and state.halted_total_at:
            tripped = datetime.fromisoformat(state.halted_total_at).date()
            if (now.astimezone(timezone.utc).date() - tripped).days >= self.halt_reset_days:
                state.halted_total = False
                state.halted_total_at = ""
                # Re-base the loss limits on the current equity, as an owner restarting the bot would.
                state.initial_capital = state.equity()
                state.peak_equity = state.equity()
        return True

    def update_halts(self, state: PortfolioState) -> list[str]:
        """Re-check loss limits on marked-to-market equity. Returns new halt reasons."""
        p = self.profile
        events: list[str] = []
        equity = state.equity()
        total_floor = state.initial_capital * (1 - p.total_loss_fraction)
        if not state.halted_total and equity <= total_floor:
            state.halted_total = True
            state.halted_total_at = state.day or ""
            events.append(f"TOTAL LOSS LIMIT: equity {equity:.2f} <= {total_floor:.2f}")
        if equity > state.peak_equity:
            state.peak_equity = equity
        if p.max_peak_drawdown > 0 and not state.halted_total and state.peak_equity > 0:
            peak_floor = state.peak_equity * (1 - p.max_peak_drawdown)
            if equity <= peak_floor:
                state.halted_total = True
                state.halted_total_at = state.day or ""
                events.append(f"PEAK DRAWDOWN LIMIT: equity {equity:.2f} <= {peak_floor:.2f} ({p.max_peak_drawdown:.0%} below peak {state.peak_equity:.2f})")
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
        if self.stale_data:
            return Decision(False, "market data stale, no new buys")
        if self.severe_event:
            return Decision(False, f"severe verified event, no new buys: {self.severe_event}")
        if state.halted_total:
            return Decision(False, "total loss limit reached, trading halted")
        if state.halted_daily:
            return Decision(False, "daily loss limit reached, no new positions today")
        held = state.positions.get(intent.product)
        dca = self._dca_mode(intent)
        if dca and p.max_dca_levels <= 0:
            return Decision(False, f"DCA entries are disabled in profile {p.name}")
        if held is not None and held.qty > 0:
            if not (dca and intent.signal.add):
                return Decision(False, "already holding this product")
            return self._review_dca_add(intent, state, held)
        if intent.signal.add:
            return Decision(False, "no position to add to")
        if state.new_positions_today >= p.max_new_positions_per_day:
            return Decision(False, f"max {p.max_new_positions_per_day} new positions per day reached")

        # Cost-aware edge: the expected move must clearly beat the round trip.
        required = c.round_trip * p.min_edge_multiple
        if intent.signal.expected_move < required:
            return Decision(False, f"expected move {intent.signal.expected_move:.3%} < required {required:.3%} after costs")

        capital = state.initial_capital
        max_position = capital * p.max_position_fraction
        if dca:
            max_position /= 1 + p.max_dca_levels           # first rung of the ladder only
        sized = self._size(intent, state, max_position)
        if isinstance(sized, Decision):
            return sized
        qty, fill_estimate, spec = sized
        if dca:
            stop = intent.price * (1 - p.dca_stop_pct)
            take = intent.price * (1 + p.dca_take_profit_pct)
        elif self._atr_mode(intent) and intent.signal.atr > 0:
            stop = intent.price - p.atr_multiple * intent.signal.atr
            take = 0.0                                   # no target: the trend decides the exit
        else:
            stop = intent.price * (1 - p.stop_loss_pct)
            take = intent.price * (1 + p.take_profit_pct)
        if spec:
            stop, take = spec.round_price(stop), spec.round_price(take)
        return Decision(True, "approved", qty=qty, notional=qty * fill_estimate * (1 + c.fee_rate),
                        stop_price=max(stop, 0.0), take_profit_price=max(take, 0.0))

    def _review_dca_add(self, intent: OrderIntent, state: PortfolioState, pos: Position) -> Decision:
        """One more rung of a DCA ladder. Same caps as a new position: the whole ladder fits in max_position."""
        p, c = self.profile, self.costs
        required = c.round_trip * p.min_edge_multiple
        if intent.signal.expected_move < required:
            return Decision(False, f"expected move {intent.signal.expected_move:.3%} < required {required:.3%} after costs")
        if pos.dca_level >= p.max_dca_levels:
            return Decision(False, f"max {p.max_dca_levels} DCA levels reached")
        reference = pos.last_entry_price or pos.entry_price
        trigger = reference * (1 - p.dca_step_pct)
        if intent.price > trigger:
            return Decision(False, f"price {intent.price:.2f} above next DCA level {trigger:.2f}")
        if pos.stop_price > 0 and intent.price <= pos.stop_price:
            return Decision(False, "price at or below the hard stop, no DCA add")
        capital = state.initial_capital
        rung = capital * p.max_position_fraction / (1 + p.max_dca_levels)
        coin_room = capital * p.max_position_fraction - pos.qty * pos.entry_price
        sized = self._size(intent, state, min(rung, coin_room))
        if isinstance(sized, Decision):
            return sized
        qty, fill_estimate, spec = sized
        # The take-profit follows the new average entry; the hard stop is not touched.
        avg = (pos.qty * pos.entry_price + qty * fill_estimate) / (pos.qty + qty)
        take = avg * (1 + p.dca_take_profit_pct)
        if spec:
            take = spec.round_price(take)
        return Decision(True, f"DCA level {pos.dca_level + 1} approved", qty=qty,
                        notional=qty * fill_estimate * (1 + c.fee_rate), stop_price=pos.stop_price, take_profit_price=take)

    def _size(self, intent: OrderIntent, state: PortfolioState, max_position: float) -> Decision | tuple[float, float, ProductSpec | None]:
        """Budget and quantity for a buy, after every cap. Returns a refusal or (qty, fill_estimate, spec)."""
        p, c = self.profile, self.costs
        capital = state.initial_capital
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
        return qty, fill_estimate, spec

    @staticmethod
    def _atr_mode(intent: OrderIntent) -> bool:
        return getattr(intent, "stop_mode", "pct") == "atr"

    @staticmethod
    def _dca_mode(intent: OrderIntent) -> bool:
        return getattr(intent, "stop_mode", "pct") == "dca"

    # ---- external evidence ---------------------------------------------------

    def apply_evidence(self, evidence: list[Evidence], now: datetime) -> str:
        """Block new buys while a verified, severe, recent event exists. Returns the blocking reason or ''."""
        reason = ""
        for e in evidence:
            try:
                published = datetime.fromisoformat(e.published_at)
            except ValueError:
                continue
            if published.tzinfo is None or published > now:
                continue                                      # unverifiable timestamp: ignored, not trusted
            age_h = (now - published).total_seconds() / 3600
            if e.verified and e.severity >= self.severe_event_threshold and age_h <= self.max_evidence_age_hours:
                reason = f"{e.category} ({e.source}, severity {e.severity:.1f}, {age_h:.0f} h ago)"
                break
        self.severe_event = reason
        return reason

    # ---- protective exits ----------------------------------------------------

    def protective_exit(self, position: Position, candle: Candle) -> OrderIntent | None:
        """Stop-loss / take-profit check against the latest closed candle. A zero level means 'none'."""
        if position.qty <= 0:
            return None
        if position.stop_price > 0 and candle.low <= position.stop_price:
            # A gap through the stop fills at the open, not at the stop: the simulator must not flatter us.
            fill = min(position.stop_price, candle.open) if candle.open > 0 else position.stop_price
            gap = " after a gap" if fill < position.stop_price else ""
            sig = Signal(position.product, Side.SELL, "risk", 1.0, 0.0,
                         f"stop-loss {position.stop_price:.2f} hit{gap} (low {candle.low:.2f}, fill ~{fill:.2f})")
            return OrderIntent(position.product, Side.SELL, fill, sig, sig.reason)
        if position.take_profit_price > 0 and candle.high >= position.take_profit_price:
            fill = max(position.take_profit_price, candle.open) if candle.open > 0 else position.take_profit_price
            sig = Signal(position.product, Side.SELL, "risk", 1.0, 0.0, f"take-profit {position.take_profit_price:.2f} hit (high {candle.high:.2f})")
            return OrderIntent(position.product, Side.SELL, fill, sig, sig.reason)
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
