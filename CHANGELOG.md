# Changelog

Version numbers follow the client's development plan of 16 Oct 2026 (0.3 … 1.0).

## 0.7.0 – 16 Oct 2026

Maps to plan phases 0.3, 0.5, 0.6 and the start of 0.7.

- Risk engine fully integrated in signal, order and paper-trading flow; tests for API outages,
  price gaps, stale data, restarts and duplicate orders (plan 0.3).
- Trend following, momentum and mean reversion built and compared; only slow trend-hold on
  6 h candles survives fees (plan 0.5; see docs/RESEARCH-2026-10.md).
- Two-year backtests with out-of-sample split, fees, slippage, maker/taker, market phases,
  buy-and-hold benchmark (plan 0.6).
- Paper trading on the EU server since 16 Oct 10:23 UTC; dashboard (DE/EN), alerting via
  webhook, restart recovery, daily backups, automatic release gate (plan 0.7, running).
- New risk profile `client` = the plan's preliminary limits (10 % / 30 % / 2 % / 8 %), configured.
- Stale market data blocks new buys; gaps through a stop fill at the open.
- Dashboard shows net return, max drawdown and data quality.
- "Server command" workflow gains `set-env` (whitelisted keys) and `show-env`.

Not built, by decision: plan phase 0.4 (news, geopolitics, whitepapers, tokenomics). See the
research report: the measurable edge so far comes from slow trend following; an information
layer is a separate, costed experiment, not a prerequisite for the pilot.

## 0.2.0 – 16 Oct 2026

Full rebuild of the v0.1 prototype: strategies, risk controller, backtester, paper trading,
dashboard, gated live broker, Docker, 52 tests.

## 0.1.0

Client's prototype (public candles, SMA signal, SQLite log).
