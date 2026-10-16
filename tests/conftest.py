import random
from datetime import datetime, timezone

import pytest

from trader.config import PROFILES, Costs
from trader.models import Candle, PortfolioState
from trader.risk import RiskController
from trader.store import Store

T0 = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())
G = 300


def make_candles(n: int, start_price: float = 100.0, drift: float = 0.0, vol: float = 0.002, seed: int = 1,
                 volume: float = 10.0, t0: int = T0) -> list[Candle]:
    rnd = random.Random(seed)
    rows, p = [], start_price
    for i in range(n):
        o = p
        p *= 1 + drift + rnd.gauss(0, vol)
        h = max(o, p) * (1 + abs(rnd.gauss(0, vol / 2)))
        lo = min(o, p) * (1 - abs(rnd.gauss(0, vol / 2)))
        rows.append(Candle(t0 + i * G, o, h, lo, p, volume))
    return rows


@pytest.fixture
def now():
    return datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def costs():
    return Costs(fee_rate=0.006, slippage_rate=0.001)


@pytest.fixture
def risk(costs):
    return RiskController(PROFILES["medium"], costs)


@pytest.fixture
def state(now, risk):
    st = PortfolioState(cash=1000.0, initial_capital=1000.0)
    risk.roll_day(st, now)
    return st


@pytest.fixture
def store():
    return Store(":memory:")
