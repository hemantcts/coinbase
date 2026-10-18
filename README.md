# Trading Agent

Autonomous crypto spot-trading agent, trading on Coinbase through its public and Advanced Trade APIs, with an independent risk controller,
three strategies, backtesting, paper trading, a web dashboard with an emergency
stop, an automatic release gate and a gated live mode.

Version 0.2 implements the full architecture from the project brief. It ships
with real orders **locked**: the live broker refuses to start unless the release
gate has passed and the owner has armed it by hand (see *Going live*).

This is educational software, not investment advice. Crypto can lose a large
part of its value in a day. The loss limits are shut-off thresholds, not
guarantees: gaps, outages and unfilled orders can cause larger losses.

## How it works

```
market data ──> strategies ──> risk controller ──> broker ──> ledger ──> SQLite ──> dashboard
 (Coinbase       trend          position caps        paper      cash,     orders,    equity, trades,
  public API)    momentum       exposure/reserve     or live    positions fills,     events, health,
                 mean rev.      loss limits                     P&L       state     STOP / FLATTEN
                 ensemble       cost-aware edge
```

* **Strategies** (`trader/strategies/`) only see candles and return a signal
  with an *estimate* of the favourable move. They never see the account.
  `trendhold` (default) is a slow EMA 20/100 trend-hold on 6-hour candles with
  a wide ATR trailing stop and no target. `trend`, `momentum` and
  `mean_reversion` are the fast intraday rules from the brief; they are kept
  for comparison because on real data they lose to fees (see *Results*).
* **Risk controller** (`trader/risk.py`) reviews every order: max position,
  max invested, cash reserve, risk per trade, daily and total loss limits,
  max new positions per day, exchange minimums, and a cost filter: the
  expected move must beat maker fee in, taker fee out and slippage by a
  safety margin. A halt blocks new buys; exits, stop-loss and take-profit
  always go through. A total-loss halt pauses for `HALT_RESET_DAYS`, then
  resumes with the limit re-based.
* **Orders**: entries and planned exits are post-only limit orders (maker
  fee), re-priced up to `ORDER_RETRIES` times if unfilled, marketable on the
  last attempt. Stop-loss and flatten are marketable at once (taker fee).
* **Data quality**: if the newest candle is older than two candle lengths (`MAX_DATA_AGE`),
  the engine logs it, alerts, and the risk controller blocks new buys until fresh data arrives.
  Exits keep working. A gap through a stop fills at the open, not at the stop.
* **Engine** (`trader/engine.py`) runs one cycle per closed candle. Orders get
  deterministic client ids, are written to the database *before* submission
  and never re-run for a candle that was already processed. Restarts restore
  cash, positions and last prices atomically.
* **Ledger** (`trader/portfolio.py`) applies fills: same code for paper and live.
* **FX** (`trader/fx.py`): the account thinks in CHF, the pairs trade in EUR.
  The CHF→EUR rate is fetched from ECB reference rates (frankfurter.app) and
  stored with its timestamp; every snapshot records the rate it used.

## Results (two years of BTC-EUR + ETH-EUR, 16 Oct 2024 – 16 Oct 2026, 0.6 % fee, 0.1 % slippage)

Full report with method, tables and the client-facing conclusions: [docs/RESEARCH-2026-10.md](docs/RESEARCH-2026-10.md).
The candle history is committed under `data/history/` so every number is reproducible.

Costs: maker 0.6 % on entries and planned exits, taker 1.2 % on stop-loss exits, 0.1 % slippage per fill
(Coinbase entry tier, the worst case). Total-loss stop pauses 30 days, then resumes.

| strategy | candles | net | worst vs start | peak-to-trough DD | trades | fees |
|---|---|---|---|---|---|---|
| buy & hold, 2 × 25 % | – | +2.1 % | | ~30 % | 0 | 0 |
| **trendhold EMA 20/100, medium** | 6 h | **+10.2 %** | −2.9 % | 18.6 % | 35 | 137 |
| trendhold, low / high | 6 h | +1.0 % / +27.0 % | −3.9 % / −5.0 % | 12.4 % / 24.2 % | 47 / 32 | 118 / 145 |
| same, next fee tier (0.35 % / 0.75 %) | 6 h | +16.8 % | −1.8 % | 16.5 % | 37 | 87 |
| trend (EMA + volume) | 6 h | −9.0 % | loss limit hit | | 16 | 47 |
| momentum | 6 h | −7.6 % | loss limit hit | | 20 | 60 |
| mean reversion | 6 h | −2.7 % | | 4.6 % | 12 | 36 |
| any strategy | 1 h or faster | −1 % to −11 % | | | | fees and noise stops |

Half-year by half-year, trend-hold 20/100 took roughly half of the market's upside and avoided more
than half of its downside, in every window. Out-of-sample (last ten months, into the bear market) the
medium profile dipped to −12 %, paused 30 days at the loss limit, resumed and ended −2.4 % against
−13.5 % for the market. The three fast strategies have no measurable edge at any timeframe or fee level.
The fee and loss-limit settings were decided on 16 Oct 2026; see section 7 of the report.

```bash
python -m trader import-history
python -m trader backtest --strategy all --profile medium --days 730 --granularity 21600
python -m trader backtest --strategy trendhold --profile all --days 730 --granularity 21600 --walk-forward
```

## Risk profiles

Limits are fractions of the starting capital so the same profile works for
any amount. `python -m trader profiles` prints them in money:

| profile | max position | max invested | reserve | risk/trade | daily loss | total loss | new/day | fixed stop / target | ATR trail | edge vs costs |
|---|---|---|---|---|---|---|---|---|---|---|
| **client** (configured) | 10 % | 30 % | 70 % | 0.25 % | 2 % | 8 % | 3 | 2 % / 4 % | 6 × ATR | 1.5× |
| low    | 15 % | 40 % | 60 % | 0.25 % | 1 % | 5 %  | 2 | 1.5 % / 3 % | 4 × ATR | 2.0× |
| medium | 25 % | 60 % | 40 % | 0.5 %  | 2 % | 8 %  | 4 | 2 % / 4 %   | 6 × ATR | 1.5× |
| high   | 35 % | 80 % | 20 % | 1 %    | 4 % | 15 % | 6 | 3 % / 6 %   | 8 × ATR | 1.2× |

The fixed stop and target apply to the fast strategies. `trendhold` uses the
trailing ATR stop (raised after every closed candle, never lowered) and no target.

`client` is the client's preliminary limit set from the plan of 16 Oct 2026 (CHF 100 per position, CHF 300
invested, CHF 20 daily, CHF 80 total) and is what paper trading runs with. `medium` is the first brief
(250 / 600 / 400 / 5 / 20 / 80 CHF). Two years of history with `client`: +4.1 % vs +1.2 % buy-and-hold at the
same exposure, worst dip 1.2 %, max drawdown 8.3 %, 35 trades, fees EUR 55.
The live pilot additionally caps every profile at 10 % per position and 20 % invested (CHF 100 / 200).

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # edit as needed
pytest -q                                             # 50 tests

python -m trader profiles                             # limits in CHF
python -m trader fetch-history --days 730 --granularity 21600   # cache 6-hour candles (or import-history)
python -m trader backtest --strategy all --profile all --days 365
python -m trader backtest --strategy trendhold --profile medium --days 365 --walk-forward --json backtest.json
python -m trader resample-history --source 3600 --target 21600  # build 6h candles from cached 1h ones
python -m trader paper                                # paper trade on live market data (Ctrl-C to stop)
python -m trader dashboard                            # http://127.0.0.1:8080 (outside Docker)
python -m trader status
python -m trader control stop | resume | flatten
```

Set `DATA_DIR=./data` in `.env` when running outside Docker.

## Backtesting

`backtest` replays cached candles through the exact same engine, risk
controller and paper broker, with fees and slippage. It reports net return,
max drawdown, round trips, win rate, profit factor, fee drag, a daily-return
Sharpe ratio, time in market and how many exits were stop-loss or take-profit.
`--walk-forward` splits the period 60/40 into in-sample and out-of-sample.
A strategy that only looks good in-sample is curve-fit; the gate uses the
out-of-sample half.

## Release gate: Phase A, B, C

The agent will not trade real money until `python -m trader gate` passes.
It writes `data/gate_result.json`, valid for 7 days and tied to the configured
strategy and profile.

**Phase A, historical simulation** (out-of-sample half of the backtest)
positive net return · max drawdown below the total loss limit · at least 5
round trips · no total-loss halt · fees below half of the gross profit.

**Phase B, paper trading** (at least 72 h, read from the paper database)
duration ≥ 72 h · no gap > 30 min between cycles · still running · error rate
≤ 2 % · no duplicate positions · no buys after a halt on the same day · FX
rate recorded from the live source · database never ran in live mode.

**Phase C, real-money pilot** then starts with the pilot caps (CHF 100 per
position, CHF 200 invested) and the owner raises limits by hand after review.

## Going live

Every one of these is required; any missing one makes `python -m trader live` refuse to start.

1. Coinbase account: create a **separate portfolio** for the agent and an
   Advanced Trade API key scoped to it with **view + trade only**. Never grant
   transfer or withdrawal rights. Restrict the key to the server IP if offered.
2. `pip install coinbase-advanced-py`, set `COINBASE_API_KEY` and
   `COINBASE_API_SECRET` in `.env` (never commit them), then run
   `python -m trader verify-live`. It checks the key permissions, prints
   balances and the product limits, and fails if the key can transfer funds.
3. Read the real fee tier from the account and set `FEE_RATE` to match.
4. `python -m trader gate` must print `RESULT: PASS`.
5. Use a **fresh database** for live: `DB_PATH=/data/live.sqlite3`.
6. Set `LIVE_TRADING=true` and write the arming sentence into `data/LIVE_ARMED`:
   `I approve live trading with real money and accept the risk of loss.`
7. Start with `python -m trader live` (or change the compose command).

Emergency: `python -m trader control stop` (no new buys, exits continue),
`control flatten` (sell everything, then stop), `control disarm` (delete the
arming file and stop). The dashboard has the same three buttons, protected
by `DASHBOARD_TOKEN`. Disabling the API key in Coinbase stops everything.

## Deployment (EU VPS, Docker)

Pushes to `main` deploy automatically through GitHub Actions: tests, then rsync
over SSH, then `deploy/server-setup.sh` on the server (Docker install if
missing, `.env` with a random dashboard token on first run, `docker compose up`).
One-time setup of the SSH key, the repository secrets and the CloudPanel reverse
proxy for the dashboard is in [deploy/README.md](deploy/README.md).

By hand on any Ubuntu server:

```bash
sudo mkdir -p /opt/coinbase-trader && sudo rsync -a --exclude .git ./ /opt/coinbase-trader/
sudo bash /opt/coinbase-trader/deploy/server-setup.sh
ssh -L 8090:127.0.0.1:8090 user@server      # then open http://127.0.0.1:8090
```

The containers run read-only, unprivileged, with all capabilities dropped. The
dashboard port is bound to localhost on the host; reach it through an SSH
tunnel or a CloudPanel reverse proxy with HTTPS and basic auth. Harden the
host: SSH keys only, firewall with no inbound app ports, automatic security
updates, encrypted backups of `data/`.

## Layout

```
trader/
  config.py        settings from .env, risk profiles, pilot caps
  models.py        Candle, Signal, Order, Position, PortfolioState
  indicators.py    SMA/EMA/RSI/ATR/z-score/volatility, no lookahead
  strategies/      trend, momentum, mean_reversion, ensemble
  risk.py          risk controller + exchange product constraints
  execution/       broker interface, paper broker, Coinbase Advanced Trade (gated)
  portfolio.py     ledger: fills -> cash, positions, realised P&L
  store.py         SQLite: candles, orders, state, snapshots, events, FX
  fx.py            CHF/EUR with timestamps
  data.py          Coinbase public candles, history download, backtest source
  engine.py        the cycle: data -> risk -> execute -> persist
  backtest.py      metrics, strategy comparison, walk-forward
  gate.py          Phase A/B checks, gate_result.json
  dashboard/       FastAPI + single-page UI with emergency controls
  cli.py           python -m trader ...
tests/             50 unit and integration tests
```

## Known limitations

* Paper fills use candle close ± slippage, not the live order book.
* Two years of history is one market cycle. Treat the backtest as a plausibility
  check, not a forecast, and judge the agent on the 30-day paper run and the
  small-money pilot.
* The ensemble opens on the strongest cost-adjusted signal; there is no
  trained predictive model yet. Whether one adds value is a measurement to
  run with the backtester, not an assumption.
* SQLite is fine for one account. A multi-tenant product needs PostgreSQL and
  per-user keys; the code is structured so that is a storage swap, not a rewrite.
* Live mode uses marketable limit orders cancelled after 3 minutes if unfilled;
  partial fills are booked as positions.

## Naming

The product is called "Trading Agent". "Coinbase" is the exchange it trades on and is only ever
used descriptively; it must not appear in the product name, the domain, logos or page titles
(trademark notice received 17 Oct 2026, complied with).
