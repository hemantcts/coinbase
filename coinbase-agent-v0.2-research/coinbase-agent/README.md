# Coinbase Paper Trading Agent v0.1

A research prototype with **no authenticated Coinbase connection and no live trading code**. Uses public Coinbase Exchange candle data for BTC-EUR and ETH-EUR. Strategy is deterministic SMA/momentum, **not a trained AI model**.

## Quick start (European VPS with Docker)

```bash
cp .env.example .env
mkdir -p data
chmod 700 data
# Edit .env if needed; LIVE_TRADING must remain false.
docker compose up -d --build
docker compose logs -f trader
```

Run tests locally: `python -m pip install -r requirements.txt && pytest -q`.

Backtest an existing historical CSV (chronological, columns `timestamp,close`, timestamps ISO 8601 UTC):

```bash
python -m agent.main backtest historical.csv --capital 1000 --fee 0.006 --slippage 0.001
```

Results are in EUR, not CHF. The initial EUR 1000 is a **placeholder**, not equivalent to CHF 1000. Before funding or reporting CHF P&L, implement audited CHF/EUR FX conversion with timestamps and conversion costs.

## Current scope and important limitations

- Coinbase Exchange public REST candles only; does not use Coinbase Advanced Trade authenticated API. Product availability and API behavior must be verified at deployment. Requests can be rate limited.
- Paper trades are simulated using candle close plus configured slippage and fees, not actual bid/ask or fills. No claim of profitability.
- Only a baseline trend/momentum signal is implemented. No LLM, trained predictive model, sentiment or optimization yet.
- Data gaps, delistings, latency, spread, minimum order size and exchange constraints are not modeled.
- The 2% daily and 8% overall limits halt **all** trading including sales in this prototype; this is a conservative fail-closed paper behavior, **not** an adequate live risk/exit policy.
- Day-start equity restoration on restart is approximate; persisted holdings can be temporarily valued at zero before first successful refresh. For serious paper/live deployment, persist last prices, true day-start equity, order IDs, candle IDs and durable trading state, and restore atomically.
- Repeated candle after a process restart may be reconsidered; position duplication is prevented only for held assets, not for all possible state transitions. Persist processed candle IDs for production.
- The system is not production-ready. A production build needs robust reconciliation, idempotent order submission, minimum sizes, exchange precision, authenticated read-only verification, separate execution service, secrets management, monitoring, alerting, stop-loss handling, comprehensive tests and manual live enablement.
- No dashboard yet; inspect `data/trades.sqlite3` and container logs.

## Deployment security

Choose an EU/EEA VPS (e.g. Frankfurt), hardened Linux host, SSH keys only, firewall with no inbound application ports, automatic security updates, encrypted backups and secret manager. Docker container is read-only, with dropped capabilities. Never store Coinbase passwords or API keys in the repository or share them in chat. Never enable withdrawal or transfer permissions. The container currently needs **no** Coinbase API key.

## Live release gate (not implemented)

1. Verify Coinbase account eligibility and applicable Swiss tax/reporting requirements.
2. Implement authenticated Advanced Trade API with a portfolio-scoped, trade-only API key, restricted privileges, secure storage and IP restrictions where supported.
3. Implement durable order state machine with client order IDs, restart-safe reconciliation, cancellation, exchange precision and minimum sizes.
4. Implement mark-to-market risk controls that prevent new risk but permit controlled liquidation, including failures and gap scenarios.
5. Implement independent emergency stop, notifications, metrics, FX accounting and fee verification.
6. Test on out-of-sample periods and run multi-day paper trading with fault injection; independently review the system.
7. Explicit owner approval for a separate live build, with tiny initial position sizes. **Changing an environment variable cannot activate live trading in this version.**

This project is educational software, not an investment recommendation. Crypto can lose substantial value.

## v0.2 research safety module (not connected to execution)
`agent/risk_v2.py` adds a deterministic, fail-closed risk decision for stale prices,
peak-to-trough drawdowns, daily losses, excessive exposure and verified severe events.
It is **not yet wired into** the original paper trading loop and must not be treated
as a deployed protection. `Evidence` inputs require a separate trustworthy ingestion
pipeline with source validation, publication timestamps, deduplication and audit logs.
No live-trading API is implemented or enabled. The initial baseline uses EUR amounts,
not CHF. A verified FX conversion and persistence of peak/day-open equity are required
before a production pilot. A 6% drawdown tripwire cannot guarantee losses stay under 6%.
A target of 10–15% monthly returns is not a realistic guaranteed outcome.
Run: `python -m pytest -q` (install pytest separately if needed).
