"""Paper broker: fills instantly at the reference price plus slippage, charges fees."""
from __future__ import annotations

from ..config import Costs
from ..models import Order, OrderStatus, Side, iso, utcnow
from .base import Broker


class PaperBroker(Broker):
    mode = "paper"

    def __init__(self, costs: Costs):
        self.costs = costs
        self._orders: dict[str, Order] = {}

    def submit(self, order: Order) -> Order:
        if order.client_order_id in self._orders:
            # Idempotent: the same client order id never fills twice.
            return self._orders[order.client_order_id]
        if order.qty <= 0 or order.price <= 0:
            order.status = OrderStatus.REJECTED
            order.reason = (order.reason + "; " if order.reason else "") + "invalid qty/price"
        else:
            slip = self.costs.slippage_rate
            fill = order.price * (1 + slip) if order.side == Side.BUY else order.price * (1 - slip)
            order.filled_qty = order.qty
            order.avg_fill_price = fill
            order.fee = order.qty * fill * self.costs.fee_rate
            order.status = OrderStatus.FILLED
        order.mode = self.mode
        order.updated_at = order.updated_at or iso(utcnow())
        self._orders[order.client_order_id] = order
        return order

    def status(self, order: Order) -> Order:
        return self._orders.get(order.client_order_id, order)

    def cancel(self, order: Order) -> Order:
        if order.status in (OrderStatus.NEW, OrderStatus.SUBMITTED):
            order.status = OrderStatus.CANCELLED
        return order
