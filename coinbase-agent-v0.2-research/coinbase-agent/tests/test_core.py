from datetime import datetime, timezone
from agent.core import Limits, Portfolio, execute_paper, signal

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)

def test_insufficient_history():
    assert signal([100]*49) == "HOLD"

def test_trade_and_position_cap():
    p = Portfolio()
    l = Limits()
    trade = execute_paper(p,"BTC-EUR","BUY",100,l,NOW)
    assert trade and trade[0] == "BUY"
    assert p.cash >= 750 - 1e-8
    assert execute_paper(p,"BTC-EUR","BUY",100,l,NOW) is None

def test_loss_limit_halts():
    p = Portfolio()
    p.last_prices["BTC-EUR"] = 100
    p.holdings["BTC-EUR"] = 10
    p.cash = 0
    p.check_limits(Limits(), NOW)
    p.last_prices["BTC-EUR"] = 90
    p.check_limits(Limits(), NOW)
    assert p.halted
    assert execute_paper(p,"BTC-EUR","SELL",90,Limits(),NOW) is None
