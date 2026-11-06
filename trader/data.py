"""Market data: Coinbase Exchange public REST (no credentials) and the local candle cache."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx

from .models import Candle
from .risk import ProductSpec
from .store import Store

PUBLIC_BASE = "https://api.exchange.coinbase.com"
GRANULARITIES = (60, 300, 900, 3600, 21600, 86400)
MAX_CANDLES_PER_REQUEST = 300
# Timeframes Coinbase does not serve, built by resampling the native one in brackets.
DERIVED = {1800: 900, 7200: 3600, 14400: 3600, 43200: 21600}
TIMEFRAME_NAMES = {60: "1m", 300: "5m", 900: "15m", 1800: "30m", 3600: "1h", 7200: "2h", 14400: "4h",
                   21600: "6h", 43200: "12h", 86400: "1d"}


def source_granularity(granularity: int) -> int:
    """The native Coinbase timeframe a timeframe is read or resampled from."""
    if granularity in GRANULARITIES:
        return granularity
    if granularity in DERIVED:
        return DERIVED[granularity]
    raise ValueError(f"Unsupported timeframe {granularity}s; use one of {sorted(set(GRANULARITIES) | set(DERIVED))}")


class DataSource(Protocol):
    def closed_candles(self, product: str, n: int, now: datetime) -> list[Candle]: ...
    def spec(self, product: str) -> ProductSpec: ...


def _parse_candles(data: object) -> list[Candle]:
    if not isinstance(data, list):
        raise RuntimeError("Unexpected Coinbase candles payload")
    rows = []
    for r in data:
        if isinstance(r, list) and len(r) >= 6:
            # Coinbase Exchange: [time, low, high, open, close, volume]
            rows.append(Candle(int(r[0]), float(r[3]), float(r[2]), float(r[1]), float(r[4]), float(r[5])))
    rows.sort(key=lambda c: c.ts)
    return rows


class CoinbasePublic:
    """Public market data. Rate limit is ~10 req/s; we stay far below."""

    def __init__(self, granularity: int = 300, timeout: float = 15.0, store: Store | None = None,
                 user_agent: str = "CoinbaseAITrader/0.2 (research)"):
        if granularity not in GRANULARITIES:
            raise ValueError(f"Unsupported granularity {granularity}; use one of {GRANULARITIES}")
        self.granularity = granularity
        self.timeout = timeout
        self.store = store
        self.headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self._specs: dict[str, ProductSpec] = {}
        self._siblings: dict[int, CoinbasePublic] = {}

    def _get(self, path: str, params: dict | None = None, retries: int = 3):
        last: Exception | None = None
        for attempt in range(retries):
            try:
                with httpx.Client(timeout=self.timeout, headers=self.headers) as client:
                    r = client.get(f"{PUBLIC_BASE}{path}", params=params)
                if r.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, ValueError) as exc:
                last = exc
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"Coinbase public API failed for {path}: {last!r}")

    def candles(self, product: str, start: datetime | None = None, end: datetime | None = None) -> list[Candle]:
        params: dict[str, str | int] = {"granularity": self.granularity}
        if start:
            params["start"] = start.astimezone(timezone.utc).isoformat()
        if end:
            params["end"] = end.astimezone(timezone.utc).isoformat()
        return _parse_candles(self._get(f"/products/{product}/candles", params))

    def closed_candles(self, product: str, n: int, now: datetime) -> list[Candle]:
        """Last n *closed* candles. The still-open one is excluded so a signal can't change later."""
        rows = self.candles(product)
        cutoff = now.timestamp()
        closed = [c for c in rows if c.ts + self.granularity <= cutoff][-n:]
        if self.store and closed:
            self.store.save_candles(product, self.granularity, closed)
        return closed

    def closed_candles_at(self, product: str, n: int, now: datetime, granularity: int) -> list[Candle]:
        """Last n closed candles of any supported timeframe (30m and 4h are resampled from 15m / 1h)."""
        if granularity == self.granularity:
            return self.closed_candles(product, n, now)
        src = source_granularity(granularity)
        sibling = self._siblings.get(src)
        if sibling is None:
            sibling = self._siblings[src] = CoinbasePublic(src, self.timeout, self.store)
        if src == granularity:
            return sibling.closed_candles(product, n, now)
        # A derived candle needs `factor` source candles: page back until there are enough (4h = 4 x 1h).
        need = (n + 1) * (granularity // src)
        rows: list[Candle] = []
        end = now
        while len(rows) < need:
            start = end - timedelta(seconds=src * MAX_CANDLES_PER_REQUEST)
            page = sibling.candles(product, start, end)
            if not page or (rows and page[0].ts >= rows[0].ts):
                break                                   # no older data: the product is younger than asked
            rows = page + [c for c in rows if c.ts > page[-1].ts]
            end = start
        rows = [c for c in rows if c.ts + src <= now.timestamp()]
        return [c for c in resample(rows, src, granularity) if c.ts + granularity <= now.timestamp()][-n:]

    def spec(self, product: str) -> ProductSpec:
        if product in self._specs:
            return self._specs[product]
        d = self._get(f"/products/{product}")
        if d.get("status") not in (None, "online"):
            raise RuntimeError(f"{product} is not online: status {d.get('status')}")
        spec = ProductSpec(
            product=product,
            base_increment=float(d.get("base_increment", 1e-8)),
            base_min_size=float(d.get("base_min_size", 0) or 0),
            quote_increment=float(d.get("quote_increment", 0.01)),
            min_market_funds=float(d.get("min_market_funds", 0) or 0),
        )
        self._specs[product] = spec
        return spec

    def fetch_history(self, product: str, days: int, store: Store, progress: bool = True) -> int:
        """Page backwards from now and cache `days` of candles. Idempotent."""
        end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        start_limit = end - timedelta(days=days)
        step = timedelta(seconds=self.granularity * MAX_CANDLES_PER_REQUEST)
        total = 0
        cursor_end = end
        while cursor_end > start_limit:
            cursor_start = max(start_limit, cursor_end - step)
            rows = self.candles(product, cursor_start, cursor_end)
            total += store.save_candles(product, self.granularity, rows)
            if progress:
                print(f"  {product}: {cursor_start:%Y-%m-%d %H:%M} .. {cursor_end:%Y-%m-%d %H:%M}  {len(rows)} candles", flush=True)
            cursor_end = cursor_start
            time.sleep(0.35)   # stay well under the public rate limit
        return total


def resample(candles: list[Candle], src_granularity: int, dst_granularity: int) -> list[Candle]:
    """Aggregate candles into a coarser timeframe aligned to UTC. Buckets with gaps are dropped."""
    if dst_granularity % src_granularity != 0 or dst_granularity <= src_granularity:
        raise ValueError("destination granularity must be a multiple of the source")
    factor = dst_granularity // src_granularity
    buckets: dict[int, list[Candle]] = {}
    for c in candles:
        buckets.setdefault(c.ts - c.ts % dst_granularity, []).append(c)
    out = []
    for ts in sorted(buckets):
        chunk = sorted(buckets[ts], key=lambda c: c.ts)
        if len(chunk) != factor:
            continue
        out.append(Candle(ts, chunk[0].open, max(c.high for c in chunk), min(c.low for c in chunk), chunk[-1].close,
                          sum(c.volume for c in chunk)))
    return out


class StoreDataSource:
    """Serves cached candles for backtests. Never returns a candle that closes after `now`."""

    def __init__(self, store: Store, granularity: int, specs: dict[str, ProductSpec] | None = None):
        self.store = store
        self.granularity = granularity
        self.specs = specs or {}
        self._cache: dict[str, list[Candle]] = {}
        self._frames: dict[tuple[str, int], list[Candle]] = {}

    def preload(self, product: str) -> list[Candle]:
        if product not in self._cache:
            self._cache[product] = self.store.candles(product, self.granularity)
        return self._cache[product]

    def closed_candles(self, product: str, n: int, now: datetime) -> list[Candle]:
        return self._closed(self.preload(product), self.granularity, n, now)

    def closed_candles_at(self, product: str, n: int, now: datetime, granularity: int) -> list[Candle]:
        """Any timeframe from the cache: stored candles if present, else resampled from the native source."""
        if granularity == self.granularity:
            return self.closed_candles(product, n, now)
        key = (product, granularity)
        if key not in self._frames:
            rows = self.store.candles(product, granularity)
            # Not cached at this timeframe: build it from the coarsest cached timeframe that divides it.
            for src in sorted((g for g in GRANULARITIES if g < granularity and granularity % g == 0), reverse=True):
                if rows:
                    break
                rows = resample(self.store.candles(product, src), src, granularity)
            self._frames[key] = rows
        return self._closed(self._frames[key], granularity, n, now)

    @staticmethod
    def _closed(rows: list[Candle], granularity: int, n: int, now: datetime) -> list[Candle]:
        cutoff = now.timestamp()
        # rows are sorted by ts; binary search for the last closed candle
        lo, hi = 0, len(rows)
        while lo < hi:
            mid = (lo + hi) // 2
            if rows[mid].ts + granularity <= cutoff:
                lo = mid + 1
            else:
                hi = mid
        return rows[max(0, lo - n):lo]

    def spec(self, product: str) -> ProductSpec:
        return self.specs.get(product, ProductSpec(product))
