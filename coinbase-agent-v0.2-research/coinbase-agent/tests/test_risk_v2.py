from datetime import datetime, timezone, timedelta
from agent.risk_v2 import evaluate, Evidence

N = datetime(2026,10,16,12,0,tzinfo=timezone.utc)
BASE = dict(now=N,latest_market_at=N,equity=1000,initial_equity=1000,day_open_equity=1000,peak_equity=1000,exposure=0,evidence=[])

def test_normal():
    assert evaluate(**BASE).allow_new_buy

def test_stale_blocks():
    assert not evaluate(**{**BASE,'latest_market_at':N-timedelta(minutes=3)}).allow_new_buy

def test_peak_drawdown_blocks():
    assert evaluate(**{**BASE,'equity':939}).reason == 'peak_drawdown_limit'

def test_event_blocks():
    event=Evidence('verified-source',N,True,.9,'security')
    assert evaluate(**{**BASE,'evidence':[event]}).reason == 'high_severity_security'

def test_exposure_blocks():
    assert evaluate(**{**BASE,'exposure':301}).reason == 'exposure_limit'
