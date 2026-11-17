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
- `STRATEGY=mtf` with `GRANULARITY=900`: analyses seven timeframes. 1d + 4h set the direction, 2h + 1h +
  30m confirm momentum, a 15m close back above its EMA 20 triggers the entry and 1m checks the price is
  calm at that moment; exits and the 6x ATR trailing stop are on 4h. 30m, 2h and 4h are resampled from
  15m / 1h. Each HOLD reason lists every timeframe as + or -, shown on the dashboard. Two-year backtest on
  BTC+ETH: +1.2 % (worst −0.8 %) vs trendhold +2.1 % (worst −1.6 %); see docs/RESEARCH-2026-11-MTF.md.

Fixes from the review of 17 Nov 2026 (each with a regression test):

- Trailing stop: if the stop timeframe (4h for mtf) cannot be fetched, the stop is kept for that cycle
  instead of being trailed on 15m candles, which raised it permanently and stopped positions out on noise.
- Candle cache: extra timeframes fetched live are no longer written to the database; backtests use the
  longest history available, so a paper run's recent daily candles cannot starve the mtf backtest or the
  gate's Phase A.
- Errors on watch-only coins are logged as `watch_error` and no longer count towards the gate's paper
  error rate; a strategy error on a watched coin can no longer interrupt a cycle.
- Dashboard: a held coin always appears in the markets table, and the list is rewritten at every engine
  start; P&L % includes the entry fee like the P&L amount; a malformed universe.json no longer causes
  HTTP 500; 15m candles show "15 min" instead of "0 h"; text from the database is escaped before display;
  after three missed refreshes the status panel shows NO CONNECTION instead of the last green status.

Open question for the client: the risk controller sizes every entry by `risk_per_trade` / `stop_loss_pct`
(2 %), while trendhold's real stop is 6 x ATR (often 8-12 %) and the DCA ladder's is 18 %. Actual risk per
trade is therefore about 1 % of capital rather than 0.25 %. Unchanged, because tightening it would cut
positions to about a fifth and change the running paper test; decide before going live.

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
