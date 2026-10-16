"""Conservative, fail-closed research gate. PAPER ONLY.
External news/whitepaper evidence must be timestamped and verified upstream.
"""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from math import isfinite

@dataclass(frozen=True)
class Evidence:
    source: str
    published_at: datetime
    verified: bool
    severity: float  # 0 benign, 1 critical
    category: str  # geopolitics, regulation, security, tokenomics, macro

@dataclass(frozen=True)
class RiskConfig:
    max_peak_drawdown: float = .06
    max_day_loss: float = .02
    max_data_age_seconds: int = 120
    max_evidence_age_hours: int = 24
    max_position_fraction: float = .10
    max_exposure_fraction: float = .30
    min_cash_fraction: float = .70
    severe_event_threshold: float = .7

@dataclass(frozen=True)
class RiskDecision:
    allow_new_buy: bool
    reason: str
    max_position_fraction: float


def evaluate(*, now: datetime, latest_market_at: datetime, equity: float,
             initial_equity: float, day_open_equity: float, peak_equity: float,
             exposure: float, evidence: list[Evidence], cfg: RiskConfig = RiskConfig()) -> RiskDecision:
    if now.tzinfo is None or latest_market_at.tzinfo is None:
        return RiskDecision(False, 'timezone_missing', 0)
    if any(not isfinite(x) or x <= 0 for x in (equity, initial_equity, day_open_equity, peak_equity)):
        return RiskDecision(False, 'invalid_equity', 0)
    if not isfinite(exposure) or exposure < 0:
        return RiskDecision(False, 'invalid_exposure', 0)
    if latest_market_at > now + timedelta(seconds=5) or now - latest_market_at > timedelta(seconds=cfg.max_data_age_seconds):
        return RiskDecision(False, 'stale_or_future_market_data', 0)
    if equity <= peak_equity * (1 - cfg.max_peak_drawdown):
        return RiskDecision(False, 'peak_drawdown_limit', 0)
    if equity <= day_open_equity * (1 - cfg.max_day_loss):
        return RiskDecision(False, 'daily_loss_limit', 0)
    if exposure / equity >= min(cfg.max_exposure_fraction, 1 - cfg.min_cash_fraction):
        return RiskDecision(False, 'exposure_limit', 0)
    for e in evidence:
        if e.published_at.tzinfo is None or e.published_at > now:
            return RiskDecision(False, 'invalid_evidence_timestamp', 0)
        if e.severity < 0 or e.severity > 1:
            return RiskDecision(False, 'invalid_evidence_severity', 0)
        if e.verified and e.severity >= cfg.severe_event_threshold and now-e.published_at <= timedelta(hours=cfg.max_evidence_age_hours):
            return RiskDecision(False, 'high_severity_'+e.category, 0)
    return RiskDecision(True, 'risk_checks_passed', cfg.max_position_fraction)
