"""The trading loop. One `run_cycle` per candle; the same code runs paper, backtest and live."""
from __future__ import annotations

import time
import traceback
from datetime import datetime
from typing import Any

from .config import Settings
from .data import DataSource
from .execution.base import Broker
from .fx import FxService
from .models import Candle, Decision, Order, OrderIntent, OrderStatus, PortfolioState, Side, Signal, iso, utcnow
from .notify import Notifier
from .portfolio import apply_fill
from .risk import RiskController
from .store import Store
from .strategies.base import Strategy


def candle_ts_from_order_id(client_order_id: str) -> int | None:
    """Client order ids look like '<mode>-<product>-<candle_ts>-<tag>[-r<n>]'; products contain hyphens."""
    parts = client_order_id.split("-")
    if len(parts) >= 2 and parts[-1].startswith("r") and parts[-1][1:].isdigit():
        parts = parts[:-1]
    try:
        return int(parts[-2])
    except (IndexError, ValueError):
        return None


class Engine:
    def __init__(self, settings: Settings, store: Store, broker: Broker, strategy: Strategy, risk: RiskController,
                 data: DataSource, fx: FxService | None = None, notifier: Notifier | None = None,
                 mode: str = "paper", verbose: bool = True, order_timeout: int | None = None):
        self.settings = settings
        self.store = store
        self.broker = broker
        self.strategy = strategy
        self.risk = risk
        self.data = data
        self.fx = fx
        self.notifier = notifier or Notifier()
        self.mode = mode
        self.verbose = verbose
        self.order_timeout = order_timeout if order_timeout is not None else settings.order_timeout
        self.risk.halt_reset_days = settings.halt_reset_days if mode != "backtest" else self.risk.halt_reset_days
        self.state: PortfolioState | None = None
        self.last_signals: dict[str, Signal] = {}
        self.last_rejections: dict[str, str] = {}
        self.consecutive_errors = 0
        self.cycles = 0

    # ---- lifecycle --------------------------------------------------------------

    def init_state(self, now: datetime | None = None) -> PortfolioState:
        """Restore the persisted state or open a fresh account converted from the report currency."""
        now = now or utcnow()
        existing = self.store.load_state()
        if existing is not None:
            self.state = existing
            self._log(now, "restart", {"cash": existing.cash, "equity": existing.equity(),
                                       "positions": list(existing.positions), "day": existing.day})
            return existing
        if self.fx is not None:
            fx = self.fx.rate(now)
            cash = self.settings.starting_capital_report * fx.rate
            rate, rate_ts, source = fx.rate, fx.fetched_at, fx.source
        else:
            cash, rate, rate_ts, source = self.settings.starting_capital_report, None, "", "none"
        self.state = PortfolioState(cash=cash, initial_capital=cash, fx_rate=rate, fx_rate_ts=rate_ts)
        self.risk.roll_day(self.state, now)
        self.store.set_meta("started_at", iso(now))
        self.store.set_meta("mode", self.mode)
        self.store.set_meta("starting_capital_report", str(self.settings.starting_capital_report))
        self.store.set_meta("report_currency", self.settings.report_currency)
        self.store.set_meta("quote_currency", self.settings.quote_currency)
        self.store.set_meta("initial_capital_quote", str(cash))
        self.store.set_meta("fx_source_at_start", source)
        self.store.set_meta("risk_profile", self.risk.profile.name)
        self.store.set_meta("strategy", self.strategy.name)
        self._log(now, "start", {"capital_report": self.settings.starting_capital_report, "capital_quote": cash,
                                 "fx_rate": rate, "fx_source": source, "profile": self.risk.profile.name,
                                 "strategy": self.strategy.name, "mode": self.mode})
        self.store.save_state(self.state, iso(now), self._report_equity(now))
        return self.state

    def run_forever(self) -> None:
        self.init_state()
        self._print(f"{self.mode.upper()} MODE. profile={self.risk.profile.name} strategy={self.strategy.name} "
                    f"products={self.settings.products} poll={self.settings.poll_seconds}s")
        while True:
            now = utcnow()
            try:
                summary = self.run_cycle(now)
                self.consecutive_errors = 0
                self._print(f"{iso(now)} equity={summary['equity']:.2f} {self.settings.quote_currency} "
                            f"cash={summary['cash']:.2f} positions={summary['positions']} "
                            f"halted={summary['halted_daily'] or summary['halted_total']} stop={summary['manual_stop']}")
            except Exception as exc:
                self.consecutive_errors += 1
                self._log(now, "error", {"error": repr(exc), "trace": traceback.format_exc()[-1500:]})
                self._print(f"cycle error ({self.consecutive_errors} in a row): {exc!r}")
                if self.consecutive_errors in (3, 10, 50):
                    self.notifier.send("Trader errors", f"{self.consecutive_errors} consecutive cycle errors: {exc!r}", "error")
            self.store.set_meta("last_cycle_at", iso(utcnow()))
            self.store.set_meta("consecutive_errors", str(self.consecutive_errors))
            time.sleep(self._sleep_seconds())

    def _sleep_seconds(self) -> float:
        """Wake shortly after the next candle closes instead of a fixed sleep."""
        g = self.settings.granularity
        now = time.time()
        next_close = (int(now) // g + 1) * g
        return max(60.0, min(float(self.settings.poll_seconds), next_close - now + 5))

    # ---- one cycle -----------------------------------------------------------------

    def run_cycle(self, now: datetime) -> dict[str, Any]:
        if self.state is None:
            self.init_state(now)
        state = self.state
        assert state is not None
        s = self.settings
        self.cycles += 1
        if self.mode == "backtest":
            flatten = False
            self.risk.manual_stop = False
        else:
            self.risk.manual_stop = s.stop_file.exists()
            flatten = s.flatten_file.exists()

        if self.mode == "live":
            self._reconcile_open_orders(now)

        candles: dict[str, list[Candle]] = {}
        for product in s.products:
            try:
                rows = self.data.closed_candles(product, s.history_candles, now)
            except Exception as exc:
                self._log(now, "error", {"product": product, "error": repr(exc)})
                continue
            if rows:
                candles[product] = rows
                state.last_prices[product] = rows[-1].close
        if not candles:
            raise RuntimeError("no market data for any product this cycle")

        if self.risk.roll_day(state, now):
            self._log(now, "day_start", {"day": state.day, "equity": state.day_start_equity})
        for reason in self.risk.update_halts(state):
            self._log(now, "halt", reason)
            self.notifier.send("Trading halted", reason, "warning")

        # 1. Protective exits on the newest closed candle.
        for product, rows in candles.items():
            pos = state.positions.get(product)
            last = rows[-1]
            if pos is None or self.store.is_processed(product, last.ts):
                continue
            intent = self.risk.protective_exit(pos, last)
            if intent:
                self._execute(intent, now, last.ts, "EXIT", urgent=True)
            elif pos.trail_atr_k > 0:
                new_stop = self.risk.trail_stop(pos, rows)
                if new_stop is not None:
                    self._log(now, "trail", {"product": product, "stop": round(new_stop, 2), "close": last.close})

        # 2. Manual flatten: close everything, then keep the STOP in place.
        if flatten and state.positions:
            for product in list(state.positions):
                price = state.last_prices.get(product)
                if not price:
                    continue
                sig = Signal(product, Side.SELL, "manual", 1.0, 0.0, "FLATTEN requested")
                self._execute(OrderIntent(product, Side.SELL, price, sig, sig.reason), now, int(now.timestamp()), "FLATTEN", urgent=True)
            if not state.positions:
                try:
                    s.flatten_file.unlink()
                    s.stop_file.touch()
                except OSError:
                    pass
                self._log(now, "flatten", "all positions closed; STOP engaged")

        # 3. Strategy decisions, once per closed candle.
        for product, rows in candles.items():
            last = rows[-1]
            if self.store.is_processed(product, last.ts):
                continue
            if len(rows) < self.strategy.warmup:
                self.store.mark_processed(product, last.ts)
                continue
            pos = state.positions.get(product)
            sig = self.strategy.evaluate(product, rows, pos)
            self.last_signals[product] = sig
            if sig.side != Side.HOLD:
                self._log(now, "signal", {"product": product, "side": sig.side.value, "strategy": sig.strategy,
                                          "confidence": sig.confidence, "expected_move": sig.expected_move,
                                          "reason": sig.reason, "candle": last.ts, "price": last.close})
            if sig.side == Side.BUY and pos is None:
                self._execute(OrderIntent(product, Side.BUY, last.close, sig, sig.reason, stop_mode=self.strategy.stop_mode),
                              now, last.ts, "BUY")
            elif sig.side == Side.SELL and pos is not None:
                self._execute(OrderIntent(product, Side.SELL, last.close, sig, sig.reason), now, last.ts, "SELL")
            self.store.mark_processed(product, last.ts)

        ts = iso(now)
        self.store.save_state(state, ts, self._report_equity(now))
        if self.last_signals:
            import json
            self.store.set_meta("last_signals", json.dumps({
                k: {"side": v.side.value, "strategy": v.strategy, "reason": v.reason, "expected_move": v.expected_move,
                    "price": state.last_prices.get(k), "ts": ts}
                for k, v in self.last_signals.items()}))
        return {
            "ts": ts, "cash": state.cash, "equity": state.equity(), "exposure": state.exposure(),
            "positions": {k: round(v.qty, 8) for k, v in state.positions.items()},
            "halted_daily": state.halted_daily, "halted_total": state.halted_total,
            "manual_stop": self.risk.manual_stop, "realised_pnl": state.realised_pnl, "fees_paid": state.fees_paid,
        }

    # ---- execution -------------------------------------------------------------------

    def _execute(self, intent: OrderIntent, now: datetime, candle_ts: int, tag: str, urgent: bool = False) -> Order | None:
        state = self.state
        assert state is not None
        decision = self.risk.review(intent, state)
        if not decision.approved:
            # Log a rejection once per product until the reason changes, so the log stays readable.
            key = f"{intent.side.value}:{decision.reason.split('(')[0].strip()}"
            if self.last_rejections.get(intent.product) != key:
                self.last_rejections[intent.product] = key
                self._log(now, "rejected", {"product": intent.product, "side": intent.side.value, "reason": decision.reason,
                                            "strategy": intent.signal.strategy, "expected_move": intent.signal.expected_move})
            return None
        base = f"{self.mode}-{intent.product}-{candle_ts}-{tag}"
        previous = self.store.orders_with_prefix(base)
        order: Order | None = None
        attempt = 0
        if previous:
            last = previous[-1]
            if not last.is_done:
                order = last                       # open order from a previous run: ask the venue first
            elif last.status == OrderStatus.CANCELLED and last.filled_qty == 0 and last.attempt < self.settings.order_retries:
                attempt = last.attempt + 1         # unfilled post-only order: re-price, last attempt goes marketable
            else:
                return last                        # already handled (filled, rejected, or retries exhausted)
        if order is None:
            coid = base if attempt == 0 else f"{base}-r{attempt}"
            final = attempt >= self.settings.order_retries
            order = Order(client_order_id=coid, product=intent.product, side=intent.side, qty=decision.qty,
                          price=intent.price, status=OrderStatus.NEW, created_at=iso(now), updated_at=iso(now),
                          strategy=intent.signal.strategy, reason=intent.reason or intent.signal.reason, mode=self.mode,
                          stop_price=decision.stop_price, take_profit_price=decision.take_profit_price,
                          urgent=urgent or final, attempt=attempt)
            self.store.save_order(order)          # persisted before submission: crash-safe
            order = self.broker.submit(order)
        else:
            order = self.broker.status(order)
        order.updated_at = iso(now)
        return self._settle(order, decision, now)

    def _settle(self, order: Order, decision: Decision | None, now: datetime) -> Order:
        state = self.state
        assert state is not None
        if order.status == OrderStatus.FILLED:
            trail = self.risk.profile.atr_multiple if (decision is not None and decision.take_profit_price == 0 and order.side == Side.BUY) else 0.0
            pnl = apply_fill(state, order, decision, trail_atr_k=trail, atr_window=self.strategy.atr_window)
            self.store.save_order(order, pnl)
            detail = {"product": order.product, "side": order.side.value, "qty": order.filled_qty,
                      "price": order.avg_fill_price, "fee": order.fee, "pnl": pnl, "strategy": order.strategy,
                      "reason": order.reason, "mode": order.mode}
            self._log(now, "fill", detail)
            self._print(f"FILL {order.side.value} {order.product} qty={order.filled_qty:.8f} @ {order.avg_fill_price:.2f} "
                        f"fee={order.fee:.4f} pnl={pnl:+.2f} ({order.reason})")
            if self.mode != "backtest":
                self.notifier.send(f"{order.mode} fill", f"{order.side.value} {order.product} {order.filled_qty:.6f} @ {order.avg_fill_price:.2f} pnl {pnl:+.2f}")
        elif order.status == OrderStatus.REJECTED:
            self.store.save_order(order)
            self._log(now, "order_rejected", order.to_dict())
        else:
            self.store.save_order(order)
            self._log(now, "order_open", {"client_order_id": order.client_order_id, "status": order.status.value})
        return order

    def _reconcile_open_orders(self, now: datetime) -> None:
        """Live mode: before doing anything new, settle or cancel what is still open."""
        for order in self.store.open_orders():
            try:
                fresh = self.broker.status(order)
            except Exception as exc:
                self._log(now, "error", {"reconcile": order.client_order_id, "error": repr(exc)})
                continue
            age = (now - datetime.fromisoformat(order.created_at)).total_seconds() if order.created_at else 0
            if not fresh.is_done and age > self.order_timeout:
                fresh = self.broker.cancel(fresh)
                self._log(now, "order_cancelled", {"client_order_id": order.client_order_id, "age_s": int(age)})
            fresh.updated_at = iso(now)
            if fresh.status == OrderStatus.FILLED or (fresh.status == OrderStatus.CANCELLED and fresh.filled_qty > 0):
                if fresh.filled_qty > 0:
                    fresh.status = OrderStatus.FILLED
                self._settle(fresh, None, now)
            else:
                self.store.save_order(fresh)
                if fresh.status == OrderStatus.CANCELLED and fresh.filled_qty == 0:
                    # Let the next cycle re-evaluate this candle and re-price the order.
                    candle_ts = candle_ts_from_order_id(fresh.client_order_id)
                    if candle_ts is not None:
                        self.store.unmark_processed(fresh.product, candle_ts)
                    self._log(now, "order_retry", {"client_order_id": fresh.client_order_id, "attempt": fresh.attempt})

    # ---- helpers -----------------------------------------------------------------------

    def _report_equity(self, now: datetime) -> float | None:
        if self.fx is None or self.state is None:
            return None
        try:
            return self.fx.to_report(self.state.equity(), now)
        except Exception:
            return None

    def _log(self, now: datetime, kind: str, detail: Any) -> None:
        self.store.log(iso(now), kind, detail)

    def _print(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)
