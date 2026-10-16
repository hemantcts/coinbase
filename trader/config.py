"""Configuration: settings from the environment and the risk profiles.

Every limit is expressed as a fraction of the starting capital so the same
profile works for CHF 1'000 or CHF 10'000, and in any quote currency.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path


@dataclass(frozen=True)
class RiskProfile:
    """Hard limits the risk controller enforces. The strategy never sees these."""

    name: str
    # Position sizing (fractions of starting capital)
    max_position_fraction: float      # max per asset
    max_exposure_fraction: float      # max invested at once
    min_reserve_fraction: float       # cash that must stay untouched
    risk_per_trade_fraction: float    # max loss at the stop-loss per trade
    # Loss limits (fractions of day-start equity / starting capital)
    daily_loss_fraction: float
    total_loss_fraction: float
    max_new_positions_per_day: int
    # Protective exits. Strategies with stop_mode "pct" use the fixed percentages,
    # strategies with stop_mode "atr" use a trailing stop of atr_multiple * ATR.
    stop_loss_pct: float
    take_profit_pct: float
    atr_multiple: float
    # A signal is tradeable only if expected move >= round-trip cost * this
    min_edge_multiple: float
    # Minimum order notional in quote currency
    min_order_notional: float = 10.0

    def amounts(self, capital: float) -> dict[str, float]:
        """Translate the fractions into money for display."""
        return {
            "max_position": capital * self.max_position_fraction,
            "max_exposure": capital * self.max_exposure_fraction,
            "min_reserve": capital * self.min_reserve_fraction,
            "risk_per_trade": capital * self.risk_per_trade_fraction,
            "daily_loss_limit": capital * self.daily_loss_fraction,
            "total_loss_limit": capital * self.total_loss_fraction,
        }


# The "medium" profile is the start configuration from the project brief:
# CHF 1'000 -> max position 250, max invested 600, reserve 400, risk/trade 5,
# daily loss 20, total loss 80, max 4 new positions per day.
PROFILES: dict[str, RiskProfile] = {
    "low": RiskProfile(
        name="low",
        max_position_fraction=0.15,
        max_exposure_fraction=0.40,
        min_reserve_fraction=0.60,
        risk_per_trade_fraction=0.0025,
        daily_loss_fraction=0.01,
        total_loss_fraction=0.05,
        max_new_positions_per_day=2,
        stop_loss_pct=0.015,
        take_profit_pct=0.03,
        atr_multiple=4.0,
        min_edge_multiple=2.0,
    ),
    "medium": RiskProfile(
        name="medium",
        max_position_fraction=0.25,
        max_exposure_fraction=0.60,
        min_reserve_fraction=0.40,
        risk_per_trade_fraction=0.005,
        daily_loss_fraction=0.02,
        total_loss_fraction=0.08,
        max_new_positions_per_day=4,
        stop_loss_pct=0.02,
        take_profit_pct=0.04,
        atr_multiple=6.0,
        min_edge_multiple=1.5,
    ),
    "high": RiskProfile(
        name="high",
        max_position_fraction=0.35,
        max_exposure_fraction=0.80,
        min_reserve_fraction=0.20,
        risk_per_trade_fraction=0.01,
        daily_loss_fraction=0.04,
        total_loss_fraction=0.15,
        max_new_positions_per_day=6,
        stop_loss_pct=0.03,
        take_profit_pct=0.06,
        atr_multiple=8.0,
        min_edge_multiple=1.2,
    ),
}

# The client's preliminary limits from the plan of 16 Oct 2026: 10 % per position, 30 % invested,
# 2 % daily, 8 % portfolio stop. To be confirmed before real money; paper trading tests them.
PROFILES["client"] = RiskProfile(
    name="client",
    max_position_fraction=0.10,
    max_exposure_fraction=0.30,
    min_reserve_fraction=0.70,
    risk_per_trade_fraction=0.0025,
    daily_loss_fraction=0.02,
    total_loss_fraction=0.08,
    max_new_positions_per_day=3,
    stop_loss_pct=0.02,
    take_profit_pct=0.04,
    atr_multiple=6.0,
    min_edge_multiple=1.5,
)

# Phase C pilot: real money, but capped at CHF 100 per position and CHF 200 total
# on a CHF 1'000 account, until the first live results are reviewed.
PILOT_CAPS = {"max_position_fraction": 0.10, "max_exposure_fraction": 0.20}


def pilot_profile(base: RiskProfile) -> RiskProfile:
    return replace(
        base,
        name=f"{base.name}-pilot",
        max_position_fraction=min(base.max_position_fraction, PILOT_CAPS["max_position_fraction"]),
        max_exposure_fraction=min(base.max_exposure_fraction, PILOT_CAPS["max_exposure_fraction"]),
    )


@dataclass(frozen=True)
class Costs:
    """Trading costs the simulator and the edge filter assume.

    Coinbase Advanced Trade charges less for maker orders (resting limit orders) than for
    taker orders. Entries and planned exits are placed post-only and pay `fee_rate`;
    urgent exits (stop-loss, flatten, last-resort fills) pay `taker_fee_rate`.
    Defaults are the entry tier, the worst case for a small account. The live broker
    reads the real tier from the API and never assumes a cheaper one.
    """

    fee_rate: float = 0.006         # maker
    taker_fee_rate: float = 0.012   # taker
    slippage_rate: float = 0.001    # price impact + spread assumption per fill

    @property
    def round_trip(self) -> float:
        """Conservative cost of a trade: maker in, taker out (a stop-loss), slippage both ways."""
        return self.fee_rate + self.taker_fee_rate + 2 * self.slippage_rate


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    products: list[str] = field(default_factory=lambda: ["BTC-EUR", "ETH-EUR"])
    quote_currency: str = "EUR"          # what the pairs settle in
    report_currency: str = "CHF"         # what the owner thinks in
    starting_capital_report: float = 1000.0  # in report currency
    granularity: int = 21600             # seconds per candle (6h: the only timeframe that survived fees in testing)
    poll_seconds: int = 300              # how often to look for a newly closed candle
    history_candles: int = 300
    risk_profile: str = "client"
    strategy: str = "trendhold"
    max_data_age: int = 0                # seconds; 0 = 2 candles. Older market data blocks new buys.
    costs: Costs = field(default_factory=Costs)
    data_dir: Path = field(default_factory=lambda: Path("./data"))
    db_path: Path | None = None
    live_trading: bool = False
    halt_reset_days: int = 30            # a total-loss halt pauses trading this long, then resumes re-based; 0 = manual restart
    order_retries: int = 2               # live: re-price an unfilled post-only order this many times, then go marketable
    order_timeout: int = 180             # live: seconds before an unfilled order is cancelled
    fx_fallback_rate: float | None = None    # CHF->EUR, used only if the FX API is down
    dashboard_token: str = ""
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8080
    coinbase_api_key: str = ""
    coinbase_api_secret: str = ""
    notify_webhook: str = ""

    @property
    def profile(self) -> RiskProfile:
        return PROFILES[self.risk_profile]

    @property
    def database(self) -> Path:
        return self.db_path or (self.data_dir / "trader.sqlite3")

    @property
    def stop_file(self) -> Path:
        return self.data_dir / "STOP"

    @property
    def flatten_file(self) -> Path:
        return self.data_dir / "FLATTEN"

    @property
    def live_armed_file(self) -> Path:
        return self.data_dir / "LIVE_ARMED"

    @property
    def gate_file(self) -> Path:
        return self.data_dir / "gate_result.json"

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.products = [p.strip().upper() for p in os.getenv("PRODUCTS", ",".join(s.products)).split(",") if p.strip()]
        s.quote_currency = os.getenv("QUOTE_CURRENCY", s.quote_currency).upper()
        s.report_currency = os.getenv("REPORT_CURRENCY", s.report_currency).upper()
        s.starting_capital_report = float(os.getenv("STARTING_CAPITAL", s.starting_capital_report))
        s.granularity = int(os.getenv("GRANULARITY", s.granularity))
        s.poll_seconds = max(60, int(os.getenv("POLL_SECONDS", s.poll_seconds)))
        s.risk_profile = os.getenv("RISK_PROFILE", s.risk_profile).lower()
        if s.risk_profile not in PROFILES:
            raise ValueError(f"RISK_PROFILE must be one of {sorted(PROFILES)}")
        s.strategy = os.getenv("STRATEGY", s.strategy).lower()
        s.costs = Costs(
            fee_rate=float(os.getenv("FEE_RATE", s.costs.fee_rate)),
            taker_fee_rate=float(os.getenv("TAKER_FEE_RATE", s.costs.taker_fee_rate)),
            slippage_rate=float(os.getenv("SLIPPAGE_RATE", s.costs.slippage_rate)),
        )
        s.halt_reset_days = int(os.getenv("HALT_RESET_DAYS", s.halt_reset_days))
        s.max_data_age = int(os.getenv("MAX_DATA_AGE", s.max_data_age))
        s.order_retries = int(os.getenv("ORDER_RETRIES", s.order_retries))
        s.order_timeout = int(os.getenv("ORDER_TIMEOUT", s.order_timeout))
        s.data_dir = Path(os.getenv("DATA_DIR", str(s.data_dir)))
        if os.getenv("DB_PATH"):
            s.db_path = Path(os.environ["DB_PATH"])
        s.live_trading = _env_bool("LIVE_TRADING", False)
        if os.getenv("FX_FALLBACK_RATE"):
            s.fx_fallback_rate = float(os.environ["FX_FALLBACK_RATE"])
        s.dashboard_token = os.getenv("DASHBOARD_TOKEN", "")
        s.dashboard_host = os.getenv("DASHBOARD_HOST", s.dashboard_host)
        s.dashboard_port = int(os.getenv("DASHBOARD_PORT", s.dashboard_port))
        s.coinbase_api_key = os.getenv("COINBASE_API_KEY", "")
        s.coinbase_api_secret = os.getenv("COINBASE_API_SECRET", "")
        s.notify_webhook = os.getenv("NOTIFY_WEBHOOK", "")
        return s
