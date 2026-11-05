# Changelog

Version numbers follow the client's development plan of 16 Oct 2026 (0.3 … 1.0).

## Unreleased – 5 Nov 2026

Optional, off by default; the configured trendhold / BTC+ETH / client paper run is unchanged.

- `trader top-coins` and `PRODUCTS=auto`: top-N Coinbase coins by liquidity, volatility and trend,
  re-ranked daily; coins with an open position stay until sold.
- `STRATEGY=adaptive`: per-coin regime switching between trend, swing pullback, DCA ladder and cash.
- `RISK_PROFILE=client-dca`: client limits plus a risk-controlled DCA ladder (max 3 adds, whole ladder
  within the 10 % per-coin cap, hard stop, take-profit follows the average entry).
- Two-year backtest: adaptive on BTC+ETH +2.6 % (trendhold +2.1 %); on the top 10 both lose about 15 %.
  See docs/RESEARCH-2026-11-ADAPTIVE.md before enabling.
- Dashboard: bot status panel (RUNNING / PAUSED / STOPPED with the reason, what it is doing now,
  strategy, completed / active / pending trades, last signal, last activity, next candle), refreshing
  every 10 s; Markets table with all top-10 coins (traded or watch-only), price, state, play and reason;
  open positions show P&L %, stop-loss and take-profit.
- `WATCH_TOP_N` (default 10): watch-only coins are scored with the same strategy but never traded.
- `STRATEGY=mtf` with `GRANULARITY=900`: multi-timeframe trend. 1d + 6h set the direction, 1h + 30m
  confirm momentum, a 15m close back above its EMA 20 triggers the entry; exits and the 6x ATR trailing
  stop stay on 6h. 30m and 4h candles are resampled from 15m / 1h. Two-year backtest on BTC+ETH:
  +0.2 % vs trendhold +2.1 %; keep trendhold (docs/RESEARCH-2026-11-MTF.md).

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
