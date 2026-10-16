"""Ledger: applies fills to the portfolio state. Same code for paper and live."""
from __future__ import annotations

from .models import Decision, Order, OrderStatus, PortfolioState, Position, Side


def apply_fill(state: PortfolioState, order: Order, decision: Decision | None = None) -> float:
    """Update cash, positions and P&L for a filled order. Returns realised P&L of the fill."""
    if order.status not in (OrderStatus.FILLED, OrderStatus.PARTIAL) or order.filled_qty <= 0:
        return 0.0
    qty, price, fee = order.filled_qty, order.avg_fill_price, order.fee
    state.fees_paid += fee
    state.last_prices[order.product] = price
    if order.side == Side.BUY:
        cost = qty * price + fee
        state.cash -= cost
        existing = state.positions.get(order.product)
        if existing and existing.qty > 0:
            total = existing.qty + qty
            existing.entry_price = (existing.entry_price * existing.qty + price * qty) / total
            existing.qty = total
            existing.entry_fee += fee
        else:
            state.positions[order.product] = Position(
                product=order.product, qty=qty, entry_price=price, entry_ts=order.updated_at or order.created_at,
                strategy=order.strategy,
                stop_price=decision.stop_price if decision else order.stop_price,
                take_profit_price=decision.take_profit_price if decision else order.take_profit_price,
                entry_fee=fee,
            )
            state.new_positions_today += 1
        return 0.0
    # SELL
    pos = state.positions.get(order.product)
    proceeds = qty * price - fee
    state.cash += proceeds
    if pos is None:
        return 0.0
    sold = min(qty, pos.qty)
    share = sold / pos.qty if pos.qty else 0.0
    pnl = sold * (price - pos.entry_price) - fee - pos.entry_fee * share
    pos.entry_fee -= pos.entry_fee * share
    pos.qty -= sold
    if pos.qty <= 1e-12:
        del state.positions[order.product]
    state.realised_pnl += pnl
    return pnl
