"""Coin universe: pick the N best Coinbase products to trade from liquidity, volatility and trend.

Only public market data is used (no credentials). A product qualifies when it is online and tradeable,
is not a stablecoin, has enough 30-day volume in the quote currency, and has enough daily history for
the strategies' warm-up. Qualifying products are scored 0..1:

  liquidity   50 %  log 30-day quote volume, scaled between the smallest and largest candidate
  volatility  30 %  14-day ATR as % of price; best inside 3..8 % a day (enough movement to beat a 1.4 %
                    round trip, not so wild that a 10 % position swings the account), fading to 0 at
                    1.5 % and 15 %
  trend       20 %  half for price above its 50-day EMA, half for the 30-day change (-20 %..+20 % -> 0..1)

The result is written to data/universe.json so a restart or an API outage reuses the last good list.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import indicators as ind
from .data import _parse_candles
from .models import iso, utcnow

STABLECOINS = {"USDC", "USDT", "DAI", "EURC", "PYUSD", "UST", "TUSD", "GUSD", "BUSD", "USDS", "FDUSD", "EURT"}
WEIGHTS = {"liquidity": 0.5, "volatility": 0.3, "trend": 0.2}


@dataclass
class CoinStats:
    product: str
    volume_30d_quote: float       # 30-day volume in quote currency
    atr_pct: float                # 14-day ATR / price
    above_ema50: bool
    change_30d: float             # fraction
    days_history: int
    score: float = 0.0
    reason: str = ""


def volatility_score(atr_pct: float, lo: float = 0.015, best_lo: float = 0.03, best_hi: float = 0.08, hi: float = 0.15) -> float:
    if atr_pct <= lo or atr_pct >= hi:
        return 0.0
    if atr_pct < best_lo:
        return (atr_pct - lo) / (best_lo - lo)
    if atr_pct <= best_hi:
        return 1.0
    return (hi - atr_pct) / (hi - best_hi)


def rank(stats: list[CoinStats], n: int = 10) -> list[CoinStats]:
    """Score the candidates and return the best n, highest score first."""
    if not stats:
        return []
    logs = [math.log10(max(s.volume_30d_quote, 1.0)) for s in stats]
    lo, hi = min(logs), max(logs)
    for s, lv in zip(stats, logs):
        liq = (lv - lo) / (hi - lo) if hi > lo else 1.0
        vol = volatility_score(s.atr_pct)
        trend = 0.5 * (1.0 if s.above_ema50 else 0.0) + 0.5 * max(0.0, min(1.0, (s.change_30d + 0.2) / 0.4))
        s.score = round(WEIGHTS["liquidity"] * liq + WEIGHTS["volatility"] * vol + WEIGHTS["trend"] * trend, 4)
        s.reason = (f"liquidity {liq:.2f}, volatility {vol:.2f} (ATR {s.atr_pct:.1%}/day), "
                    f"trend {trend:.2f} ({'above' if s.above_ema50 else 'below'} EMA50, 30d {s.change_30d:+.0%})")
    return sorted(stats, key=lambda s: s.score, reverse=True)[:n]


def tradeable(p: dict[str, Any], quote: str) -> bool:
    return (p.get("quote_currency") == quote and p.get("status") == "online" and not p.get("trading_disabled")
            and not p.get("cancel_only") and not p.get("limit_only") and not p.get("fx_stablecoin")
            and p.get("base_currency", "").upper() not in STABLECOINS)


def discover(public: Any, quote: str = "EUR", n: int = 10, min_volume_quote: float = 5_000_000.0,
             min_days: int = 120, prefilter: int = 25, progress: bool = False) -> list[CoinStats]:
    """Rank the tradeable products on Coinbase. `public` is a CoinbasePublic (only its _get is used)."""
    products = [p["id"] for p in public._get("/products") if tradeable(p, quote)]
    volumes: list[tuple[str, float]] = []
    for pid in products:
        try:
            st = public._get(f"/products/{pid}/stats")
            volumes.append((pid, float(st.get("volume_30day") or 0) * float(st.get("last") or 0)))
        except RuntimeError:
            continue
        time.sleep(0.15)
    # Daily candles only for the most liquid candidates: keeps the scan to a few dozen requests.
    volumes = sorted((v for v in volumes if v[1] >= min_volume_quote), key=lambda v: v[1], reverse=True)[:prefilter]
    stats: list[CoinStats] = []
    for pid, vol in volumes:
        try:
            daily = _parse_candles(public._get(f"/products/{pid}/candles", {"granularity": 86400}))
        except RuntimeError:
            continue
        time.sleep(0.15)
        if len(daily) < min_days:
            if progress:
                print(f"  {pid}: only {len(daily)} days of history, skipped", flush=True)
            continue
        c = [d.close for d in daily]
        atr = ind.atr([d.high for d in daily], [d.low for d in daily], c, 14) or 0.0
        ema50 = ind.ema(c, 50) or c[-1]
        stats.append(CoinStats(pid, vol, atr / c[-1] if c[-1] else 0.0, c[-1] > ema50,
                               ind.pct_change(c, 30) or 0.0, len(daily)))
    return rank(stats, n)


def save(path: Path, coins: list[CoinStats], now: datetime | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ts": iso(now or utcnow()), "products": [c.product for c in coins],
                                "coins": [asdict(c) for c in coins]}, indent=2))


def load(path: Path) -> list[str]:
    try:
        return list(json.loads(path.read_text())["products"])
    except (OSError, ValueError, KeyError, TypeError):
        return []


def resolve(public: Any, path: Path, quote: str, n: int, fallback: list[str], keep: list[str] | None = None,
            log=print) -> list[str]:
    """Fresh top-n, else the last saved list, else the fallback. Products in `keep` (open positions) always stay."""
    try:
        coins = discover(public, quote, n)
        if not coins:
            raise RuntimeError("no product qualified")
        save(path, coins)
        products = [c.product for c in coins]
        log(f"universe: top {len(products)} by liquidity/volatility/trend: {', '.join(products)}")
    except Exception as exc:                      # noqa: BLE001  any failure falls back, trading must not stop
        products = load(path) or list(fallback)
        log(f"universe: selection failed ({exc!r}); using {', '.join(products)}")
    for p in keep or []:
        if p not in products:
            products.append(p)                    # a held coin that dropped out still needs its exits managed
    return products
