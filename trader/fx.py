"""Currency conversion between the report currency (CHF) and the quote currency (EUR).

Every rate is stored with the day and the fetch timestamp so a CHF figure can
always be traced back to the rate it was computed with. Sources, tried in
order: frankfurter.app (ECB reference rates as JSON), then the ECB's own daily
XML feed. If both are unreachable the configured fallback is used and flagged
as such; without a fallback the engine refuses to start and says why.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from .models import iso
from .store import Store

FRANKFURTER = "https://api.frankfurter.app"
ECB_XML = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


@dataclass(frozen=True)
class FxRate:
    base: str
    quote: str
    rate: float          # 1 base = rate quote
    day: str
    fetched_at: str
    source: str

    def convert(self, amount: float) -> float:
        return amount * self.rate

    def inverse(self) -> "FxRate":
        return FxRate(self.quote, self.base, 1 / self.rate, self.day, self.fetched_at, self.source)


class FxService:
    def __init__(self, store: Store | None = None, fallback_rate: float | None = None,
                 base: str = "CHF", quote: str = "EUR", offline: bool = False, timeout: float = 10.0):
        self.store = store
        self.fallback_rate = fallback_rate
        self.base = base.upper()
        self.quote = quote.upper()
        self.offline = offline
        self.timeout = timeout
        self._cache: dict[str, FxRate] = {}
        self._last_error = ""

    def rate(self, now: datetime | None = None) -> FxRate:
        """Rate for converting report currency -> quote currency on the given day."""
        now = now or datetime.now(timezone.utc)
        if self.base == self.quote:
            return FxRate(self.base, self.quote, 1.0, now.date().isoformat(), iso(now), "identity")
        day = now.astimezone(timezone.utc).date().isoformat()
        if day in self._cache:
            return self._cache[day]
        if self.store:
            row = self.store.get_fx(day, self.base, self.quote)
            if row:
                fx = FxRate(self.base, self.quote, row["rate"], day, row["fetched_at"], row["source"])
                self._cache[day] = fx
                return fx
        fx: FxRate | None = None
        errors: list[str] = []
        if not self.offline:
            for name, fetch in (("frankfurter", self._fetch_frankfurter), ("ecb", self._fetch_ecb)):
                try:
                    fx = fetch(day, now)
                    break
                except Exception as exc:   # network down, service down, bad payload
                    errors.append(f"{name}: {exc!r}")
        if fx is None:
            self._last_error = "; ".join(errors)
            if self.fallback_rate is None:
                raise RuntimeError(f"FX rate {self.base}->{self.quote} unavailable ({self._last_error or 'offline'}) "
                                   f"and no FX_FALLBACK_RATE configured")
            fx = FxRate(self.base, self.quote, self.fallback_rate, day, iso(now), "fallback")
        if self.store:
            self.store.save_fx(day, self.base, self.quote, fx.rate, fx.fetched_at, fx.source)
        self._cache[day] = fx
        return fx

    def _fetch_frankfurter(self, day: str, now: datetime) -> FxRate:
        with httpx.Client(timeout=self.timeout) as client:
            r = client.get(f"{FRANKFURTER}/latest", params={"from": self.base, "to": self.quote})
            r.raise_for_status()
            data = r.json()
        rate = float(data["rates"][self.quote])
        if rate <= 0:
            raise ValueError("non-positive FX rate")
        return FxRate(self.base, self.quote, rate, day, iso(now), f"frankfurter:{data.get('date', day)}")

    def _fetch_ecb(self, day: str, now: datetime) -> FxRate:
        """ECB daily reference rates, quoted as 1 EUR = x CCY."""
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            r = client.get(ECB_XML)
            r.raise_for_status()
            text = r.text
        rates = {"EUR": 1.0}
        for ccy, val in re.findall(r"currency='([A-Z]{3})'\s+rate='([0-9.]+)'", text):
            rates[ccy] = float(val)
        if self.base not in rates or self.quote not in rates:
            raise ValueError(f"ECB feed lacks {self.base} or {self.quote}")
        m = re.search(r"time='(\d{4}-\d{2}-\d{2})'", text)
        rate = rates[self.quote] / rates[self.base]      # 1 base = rate quote
        if rate <= 0:
            raise ValueError("non-positive FX rate")
        return FxRate(self.base, self.quote, rate, day, iso(now), f"ecb:{m.group(1) if m else day}")

    def to_report(self, amount_quote: float, now: datetime | None = None) -> float:
        """Convert a quote-currency (EUR) amount into the report currency (CHF)."""
        return amount_quote / self.rate(now).rate

    def to_quote(self, amount_report: float, now: datetime | None = None) -> float:
        return amount_report * self.rate(now).rate
