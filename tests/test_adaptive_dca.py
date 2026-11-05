from datetime import datetime, timezone

import pytest

from conftest import G, make_candles
from trader import universe
from trader.config import PROFILES, Costs, Settings
from trader.engine import Engine
from trader.execution.paper import PaperBroker
from trader.models import Order, OrderIntent, OrderStatus, PortfolioState, Position, Side, Signal
from trader.portfolio import apply_fill
from trader.risk import RiskController
from trader.store import Store
from trader.strategies import build
from trader.strategies.adaptive import DCA, SWING, TREND, Adaptive

NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)


def _rc(profile="client-dca"):
    return RiskController(PROFILES[profile], Costs())


def _state(rc, cash=1000.0):
    st = PortfolioState(cash=cash, initial_capital=1000.0)
    rc.roll_day(st, NOW)
    return st


def _dca_sig(add=False, expected=0.06):
    return Signal("SOL-EUR", Side.BUY, DCA, 0.5, expected, "dip", 1.0, stop_mode="dca", add=add)


# ---- risk controller ------------------------------------------------------------------

def test_client_dca_keeps_every_client_limit():
    a, b = PROFILES["client"], PROFILES["client-dca"]
    for f in ("max_position_fraction", "max_exposure_fraction", "min_reserve_fraction", "risk_per_trade_fraction",
              "daily_loss_fraction", "total_loss_fraction", "max_new_positions_per_day", "max_peak_drawdown"):
        assert getattr(a, f) == getattr(b, f), f
    assert a.max_dca_levels == 0 and b.max_dca_levels == 3


def test_dca_entry_is_refused_when_the_profile_has_no_ladder():
    rc = _rc("client")
    d = rc.review(OrderIntent("SOL-EUR", Side.BUY, 100.0, _dca_sig(), stop_mode="dca"), _state(rc))
    assert not d.approved and "disabled" in d.reason


def test_first_rung_is_a_quarter_of_the_coin_cap_with_hard_stop_and_target():
    rc = _rc()
    d = rc.review(OrderIntent("SOL-EUR", Side.BUY, 100.0, _dca_sig(), stop_mode="dca"), _state(rc))
    assert d.approved
    assert d.notional == pytest.approx(25.0, rel=0.01)           # 10 % of 1000 / (1 + 3 levels)
    assert d.stop_price == pytest.approx(82.0)                   # 18 % below the first entry
    assert d.take_profit_price == pytest.approx(106.0)


def _ladder(rc, st, price=100.0):
    d = rc.review(OrderIntent("SOL-EUR", Side.BUY, price, _dca_sig(), stop_mode="dca"), st)
    o = Order("o1", "SOL-EUR", Side.BUY, d.qty, price, OrderStatus.FILLED, filled_qty=d.qty, avg_fill_price=price,
              fee=0.15, strategy=DCA)
    apply_fill(st, o, d)
    st.last_prices["SOL-EUR"] = price
    return st.positions["SOL-EUR"]


def test_add_needs_the_next_level_and_moves_only_the_take_profit():
    rc = _rc()
    st = _state(rc)
    pos = _ladder(rc, st)
    add = lambda px: rc.review(OrderIntent("SOL-EUR", Side.BUY, px, _dca_sig(add=True), stop_mode="dca"), st)
    d = add(97.0)
    assert not d.approved and "above next DCA level" in d.reason
    d = add(95.0)
    assert d.approved and d.stop_price == pos.stop_price
    o = Order("o2", "SOL-EUR", Side.BUY, d.qty, 95.0, OrderStatus.FILLED, filled_qty=d.qty, avg_fill_price=95.0, fee=0.15, strategy=DCA)
    apply_fill(st, o, d)
    assert pos.dca_level == 1 and pos.last_entry_price == 95.0
    assert pos.stop_price == pytest.approx(82.0)
    assert pos.take_profit_price == pytest.approx(pos.entry_price * 1.06, rel=0.002) and pos.take_profit_price < 106.0
    assert st.new_positions_today == 1                           # an add is not a new position


def test_ladder_never_exceeds_the_coin_cap_or_the_level_limit():
    rc = _rc()
    st = _state(rc)
    pos = _ladder(rc, st)
    price = 100.0
    for _ in range(3):
        price *= 0.95
        d = rc.review(OrderIntent("SOL-EUR", Side.BUY, price, _dca_sig(add=True, expected=0.1), stop_mode="dca"), st)
        assert d.approved, d.reason
        apply_fill(st, Order(f"a{price}", "SOL-EUR", Side.BUY, d.qty, price, OrderStatus.FILLED, filled_qty=d.qty,
                             avg_fill_price=price, fee=0.1, strategy=DCA), d)
    assert pos.dca_level == 3
    assert pos.qty * pos.entry_price <= 100.0 + 1e-6              # 10 % of capital
    d = rc.review(OrderIntent("SOL-EUR", Side.BUY, price * 0.9, _dca_sig(add=True, expected=0.1), stop_mode="dca"), st)
    assert not d.approved and "max 3 DCA levels" in d.reason


def test_add_respects_halts_stop_and_hard_stop():
    rc = _rc()
    st = _state(rc)
    pos = _ladder(rc, st)
    intent = OrderIntent("SOL-EUR", Side.BUY, 95.0, _dca_sig(add=True), stop_mode="dca")
    rc.manual_stop = True
    assert not rc.review(intent, st).approved
    rc.manual_stop = False
    st.halted_daily = True
    assert not rc.review(intent, st).approved
    st.halted_daily = False
    below = OrderIntent("SOL-EUR", Side.BUY, pos.stop_price - 1, _dca_sig(add=True, expected=0.2), stop_mode="dca")
    assert "hard stop" in rc.review(below, st).reason


def test_a_plain_buy_still_cannot_double_a_position():
    rc = _rc()
    st = _state(rc)
    _ladder(rc, st)
    sig = Signal("SOL-EUR", Side.BUY, TREND, 0.8, 0.06, "", 1.0, stop_mode="atr")
    d = rc.review(OrderIntent("SOL-EUR", Side.BUY, 90.0, sig, stop_mode="atr"), st)
    assert not d.approved and d.reason == "already holding this product"
    lone = rc.review(OrderIntent("ETH-EUR", Side.BUY, 90.0, _dca_sig(add=True), stop_mode="dca"), st)
    assert not lone.approved and "no position to add to" in lone.reason


# ---- adaptive strategy ----------------------------------------------------------------------

def test_adaptive_trend_entry_in_an_uptrend():
    up = make_candles(200, drift=0.004, vol=0.004, seed=21)
    sig = Adaptive().evaluate("BTC-EUR", up, None)
    assert sig.side == Side.BUY and sig.strategy == TREND and sig.stop_mode == "atr" and not sig.add


def test_adaptive_stays_out_of_a_downtrend():
    down = make_candles(200, drift=-0.004, vol=0.004, seed=5)
    a = Adaptive()
    assert a.regime([c.close for c in down])[0] == "downtrend"
    assert a.evaluate("BTC-EUR", down, None).side == Side.HOLD


def _range_then_dip():
    import math
    from conftest import T0
    from trader.models import Candle
    prices = [100 * (1 + 0.04 * math.sin(i / 3)) for i in range(200)] + [97, 94, 91, 88]
    return [Candle(T0 + i * G, p, p * 1.005, p * 0.995, p, 1.0) for i, p in enumerate(prices)]


def test_adaptive_buys_a_dip_in_a_range_with_dca_and_adds_on_the_next_dip():
    dip = _range_then_dip()
    a = Adaptive()
    assert a.regime([c.close for c in dip])[0] == "range"
    sig = a.evaluate("SOL-EUR", dip, None)
    assert sig.side == Side.BUY and sig.strategy == DCA and sig.stop_mode == "dca"
    pos = Position("SOL-EUR", 1.0, dip[-3].close, "x", DCA, 0.0, 0.0)
    again = a.evaluate("SOL-EUR", dip, pos)
    assert again.side == Side.BUY and again.add


def test_adaptive_routes_exits_by_owner_and_closes_dca_in_a_downtrend():
    down = make_candles(200, drift=-0.004, vol=0.004, seed=5)
    a = Adaptive()
    for owner in (TREND, SWING, "trendhold"):
        pos = Position("BTC-EUR", 1.0, 100.0, "x", owner, 0.0, 0.0)
        assert a.evaluate("BTC-EUR", down, pos).side == Side.SELL
    pos = Position("BTC-EUR", 1.0, 100.0, "x", DCA, 0.0, 0.0)
    s = a.evaluate("BTC-EUR", down, pos)
    assert s.side == Side.SELL and "turned down" in s.reason


def test_build_knows_adaptive():
    assert build("adaptive").name == "adaptive"


# ---- engine ------------------------------------------------------------------------------

class _LadderStub:
    """Opens a DCA position, then asks to add on every candle; the risk controller decides."""
    name = "stub"
    warmup = 1
    stop_mode = "pct"
    atr_window = 14

    def evaluate(self, product, candles, position):
        return _dca_sig(add=position is not None, expected=0.1).__class__(
            product, Side.BUY, DCA, 0.5, 0.1, "stub", 1.0, stop_mode="dca", add=position is not None)


class _Feed:
    def __init__(self, rows):
        self.rows = rows

    def closed_candles(self, product, n, now):
        return [c for c in self.rows if c.ts + G <= now.timestamp()][-n:]


def test_engine_executes_dca_adds_only_at_deeper_levels():
    from conftest import T0
    from trader.models import Candle
    prices = [100, 99, 94, 93, 89, 84, 83]
    rows = [Candle(T0 + i * G, p, p, p, p, 1.0) for i, p in enumerate(prices)]
    settings = Settings(products=["SOL-EUR"], granularity=G, quote_currency="EUR", report_currency="EUR")
    store = Store(":memory:")
    eng = Engine(settings, store, PaperBroker(Costs()), _LadderStub(), _rc(), _Feed(rows), mode="backtest", verbose=False)
    for i in range(len(rows)):
        eng.run_cycle(datetime.fromtimestamp(T0 + (i + 1) * G, tz=timezone.utc))
    buys = [f for f in store.fills() if f["side"] == "BUY"]
    assert len(buys) == 4                                         # 100, 94, 89, 84: each >= 5 % below the last
    pos = eng.state.positions["SOL-EUR"]
    assert pos.dca_level == 3 and pos.stop_price == pytest.approx(82.0)
    rows.append(Candle(T0 + len(rows) * G, 81, 81, 81, 81, 1.0))   # through the hard stop: the ladder is sold
    eng.run_cycle(datetime.fromtimestamp(T0 + len(rows) * G, tz=timezone.utc))
    sells = [f for f in store.fills() if f["side"] == "SELL"]
    assert len(sells) == 1 and "stop-loss" in sells[0]["reason"]


# ---- universe ------------------------------------------------------------------------------

def test_rank_prefers_liquid_moderately_volatile_coins():
    s = [universe.CoinStats("BIG-EUR", 400e6, 0.03, True, 0.05, 300),
         universe.CoinStats("WILD-EUR", 400e6, 0.25, True, 0.05, 300),
         universe.CoinStats("THIN-EUR", 5e6, 0.04, True, 0.05, 300)]
    top = universe.rank(s, 2)
    assert [c.product for c in top] == ["BIG-EUR", "WILD-EUR"] and top[0].score > top[1].score
    assert universe.volatility_score(0.05) == 1.0 and universe.volatility_score(0.2) == 0.0


def test_tradeable_excludes_stablecoins_and_disabled_markets():
    ok = {"quote_currency": "EUR", "status": "online", "base_currency": "SOL"}
    assert universe.tradeable(ok, "EUR")
    assert not universe.tradeable({**ok, "base_currency": "USDC"}, "EUR")
    assert not universe.tradeable({**ok, "trading_disabled": True}, "EUR")
    assert not universe.tradeable({**ok, "status": "delisted"}, "EUR")
    assert not universe.tradeable({**ok, "quote_currency": "USD"}, "EUR")


def test_resolve_falls_back_and_keeps_held_coins(tmp_path):
    class Down:
        def _get(self, *a, **k):
            raise RuntimeError("offline")
    path = tmp_path / "universe.json"
    assert universe.resolve(Down(), path, "EUR", 10, ["BTC-EUR"], keep=["DOT-EUR"], log=lambda m: None) == ["BTC-EUR", "DOT-EUR"]
    universe.save(path, [universe.CoinStats("SOL-EUR", 1, 0.04, True, 0, 300)])
    assert universe.resolve(Down(), path, "EUR", 10, ["BTC-EUR"], log=lambda m: None) == ["SOL-EUR"]


def test_products_auto_from_env(monkeypatch):
    monkeypatch.setenv("PRODUCTS", "auto")
    monkeypatch.setenv("TOP_N", "7")
    s = Settings.from_env()
    assert s.auto_products and s.top_n == 7 and s.products == ["BTC-EUR", "ETH-EUR"]
