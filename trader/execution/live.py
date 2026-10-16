"""Live broker on the Coinbase Advanced Trade API. Triple-gated and never the default.

To place a single real order, ALL of the following must hold:
  1. LIVE_TRADING=true in the environment
  2. data/LIVE_ARMED exists and contains exactly the arming sentence
  3. data/gate_result.json says PASS, is younger than 7 days and matches the configured strategy/profile
  4. the API key can trade but cannot transfer (checked at start)
The pilot caps (10% per position, 20% invested) are applied on top of the profile.

Orders are marketable limit orders with a client order id. If a request is
interrupted, the order is looked up by that id before anything is resent.
"""
from __future__ import annotations

import math
from typing import Any

from ..config import Costs, Settings
from ..models import Order, OrderStatus, Side, iso, utcnow
from ..risk import ProductSpec
from .base import Broker

ARMING_SENTENCE = "I approve live trading with real money and accept the risk of loss."

_STATUS = {
    "OPEN": OrderStatus.SUBMITTED, "PENDING": OrderStatus.SUBMITTED, "QUEUED": OrderStatus.SUBMITTED,
    "FILLED": OrderStatus.FILLED, "CANCELLED": OrderStatus.CANCELLED, "CANCEL_QUEUED": OrderStatus.SUBMITTED,
    "EXPIRED": OrderStatus.CANCELLED, "FAILED": OrderStatus.REJECTED, "UNKNOWN_ORDER_STATUS": OrderStatus.UNKNOWN,
}


def _d(resp: Any) -> dict[str, Any]:
    """The SDK returns objects in newer versions and dicts in older ones."""
    if isinstance(resp, dict):
        return resp
    if hasattr(resp, "to_dict"):
        return resp.to_dict()
    if hasattr(resp, "__dict__"):
        return dict(resp.__dict__)
    return dict(resp)


def _fmt(value: float, increment: float) -> str:
    if increment <= 0:
        return f"{value:.8f}"
    decimals = max(0, -int(math.floor(math.log10(increment) + 1e-9)))
    steps = math.floor(value / increment + 1e-9)
    return f"{steps * increment:.{decimals}f}"


def assert_armed(settings: Settings) -> None:
    from ..gate import gate_status
    if not settings.live_trading:
        raise RuntimeError("LIVE_TRADING is not true")
    f = settings.live_armed_file
    if not f.exists() or f.read_text().strip() != ARMING_SENTENCE:
        raise RuntimeError(f"{f} missing or does not contain the arming sentence")
    ok, why = gate_status(settings)
    if not ok:
        raise RuntimeError(f"release gate not passed: {why}")
    if not settings.coinbase_api_key or not settings.coinbase_api_secret:
        raise RuntimeError("COINBASE_API_KEY / COINBASE_API_SECRET not set")


class CoinbaseAdvancedBroker(Broker):
    mode = "live"

    def __init__(self, settings: Settings, costs: Costs, client: Any | None = None):
        self.settings = settings
        self.costs = costs
        if client is None:
            try:
                from coinbase.rest import RESTClient  # type: ignore
            except ImportError as exc:
                raise RuntimeError("pip install coinbase-advanced-py to use the live broker") from exc
            client = RESTClient(api_key=settings.coinbase_api_key, api_secret=settings.coinbase_api_secret, timeout=15)
        self.client = client
        self._specs: dict[str, ProductSpec] = {}

    # ---- read-only -----------------------------------------------------------------

    def permissions(self) -> dict[str, bool]:
        d = _d(self.client.get_api_key_permissions())
        return {"can_view": bool(d.get("can_view")), "can_trade": bool(d.get("can_trade")),
                "can_transfer": bool(d.get("can_transfer")), "portfolio_type": str(d.get("portfolio_type", ""))}

    def balances(self) -> dict[str, float]:
        out: dict[str, float] = {}
        cursor = None
        while True:
            d = _d(self.client.get_accounts(cursor=cursor) if cursor else self.client.get_accounts())
            for a in d.get("accounts", []):
                a = _d(a)
                bal = _d(a.get("available_balance", {}))
                out[a.get("currency", "?")] = float(bal.get("value", 0) or 0)
            if not d.get("has_next"):
                break
            cursor = d.get("cursor")
        return out

    def spec(self, product: str) -> ProductSpec:
        if product not in self._specs:
            d = _d(self.client.get_product(product))
            if str(d.get("status", "online")).lower() not in ("online", ""):
                raise RuntimeError(f"{product} status {d.get('status')}")
            self._specs[product] = ProductSpec(
                product=product, base_increment=float(d.get("base_increment", 1e-8)),
                base_min_size=float(d.get("base_min_size", 0) or 0), quote_increment=float(d.get("quote_increment", 0.01)),
                min_market_funds=float(d.get("quote_min_size", 0) or 0))
        return self._specs[product]

    # ---- trading -------------------------------------------------------------------------

    def submit(self, order: Order) -> Order:
        spec = self.spec(order.product)
        slip = self.costs.slippage_rate
        limit = order.price * (1 + slip) if order.side == Side.BUY else order.price * (1 - slip)
        size = _fmt(order.qty, spec.base_increment)
        price = _fmt(limit, spec.quote_increment)
        try:
            if order.side == Side.BUY:
                resp = self.client.limit_order_gtc_buy(client_order_id=order.client_order_id, product_id=order.product,
                                                       base_size=size, limit_price=price)
            else:
                resp = self.client.limit_order_gtc_sell(client_order_id=order.client_order_id, product_id=order.product,
                                                        base_size=size, limit_price=price)
        except Exception as exc:
            # The request may have reached Coinbase. Look the order up before deciding anything.
            found = self._find_by_client_id(order)
            if found is not None:
                return found
            order.status = OrderStatus.UNKNOWN
            order.reason = f"{order.reason}; submit error {exc!r}"
            return order
        d = _d(resp)
        if d.get("success"):
            ok = _d(d.get("success_response", {}))
            order.exchange_order_id = str(ok.get("order_id", ""))
            order.status = OrderStatus.SUBMITTED
            return self.status(order)
        err = _d(d.get("error_response", {}))
        order.status = OrderStatus.REJECTED
        order.reason = f"{order.reason}; coinbase: {err.get('error', '')} {err.get('message', '')} {err.get('preview_failure_reason', '')}".strip()
        return order

    def status(self, order: Order) -> Order:
        if not order.exchange_order_id:
            found = self._find_by_client_id(order)
            if found is None:
                return order
            order = found
        d = _d(_d(self.client.get_order(order.exchange_order_id)).get("order", {}))
        return self._apply(order, d)

    def cancel(self, order: Order) -> Order:
        if order.exchange_order_id and not order.is_done:
            self.client.cancel_orders(order_ids=[order.exchange_order_id])
        return self.status(order)

    # ---- helpers -------------------------------------------------------------------------

    def _apply(self, order: Order, d: dict[str, Any]) -> Order:
        if not d:
            return order
        order.exchange_order_id = str(d.get("order_id", order.exchange_order_id))
        order.status = _STATUS.get(str(d.get("status", "")).upper(), OrderStatus.UNKNOWN)
        order.filled_qty = float(d.get("filled_size", 0) or 0)
        order.avg_fill_price = float(d.get("average_filled_price", 0) or 0)
        order.fee = float(d.get("total_fees", 0) or 0)
        if order.status == OrderStatus.SUBMITTED and order.filled_qty > 0:
            order.status = OrderStatus.PARTIAL
        order.updated_at = iso(utcnow())
        return order

    def _find_by_client_id(self, order: Order) -> Order | None:
        try:
            d = _d(self.client.list_orders(product_ids=[order.product], limit=50))
        except Exception:
            return None
        for o in d.get("orders", []):
            o = _d(o)
            if o.get("client_order_id") == order.client_order_id:
                return self._apply(order, o)
        return None
