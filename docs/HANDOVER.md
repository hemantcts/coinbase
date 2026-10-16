# Handover notes for the next developer

Project: Coinbase AI Trader. Autonomous spot-trading agent, Python 3.12, SQLite, FastAPI, Docker.
Repo: https://github.com/hemantcts/coinbase (branch `main` is production).
Live: https://coinbase.loewenmut.ch on the client's Lightsail VPS (Ubuntu, CloudPanel, Docker).

## 1. Read these first, in this order (30 minutes)

1. `README.md` – what it is, how it works, how to run it.
2. `docs/RESEARCH-2026-10.md` – why the strategy is what it is, what failed, the numbers, the decisions taken.
3. `deploy/README.md` – server, secrets, deploy, dashboard.
4. `trader/engine.py` – the one loop everything runs through. ~300 lines.

## 2. Where we are

| phase | status |
|---|---|
| 1 Development | done |
| 2 Two-year historical test | passed |
| 3 Paper trading, 30 days | running since 16 Oct 2026 10:23 UTC, review ~15 Nov |
| 4 Real-money pilot, CHF 200 | needs client's Coinbase portfolio + trade-only API key |
| 5 Full CHF 1 000 | after a clean pilot |

Configured decisions (all in `.env`, see `.env.example`): strategy `trendhold` (EMA 20/100 on 6-hour
candles, 6×ATR trailing stop), profile `medium`, fees assumed maker 0.6 % / taker 1.2 %,
total-loss halt pauses 30 days (`HALT_RESET_DAYS=30`).

## 3. Daily work, no SSH needed

- **Deploy**: push to `main`. GitHub Actions runs the tests, copies the code to the server and restarts
  the containers. A failed test blocks the deploy. The server's `.env` and `data/` are never overwritten.
- **Operate**: GitHub → Actions → "Server command" → Run workflow → pick `status`, `logs`, `events`,
  `gate`, `backtest`, `containers`, `restart`, `stop`, `resume`, `flatten`. Output is in the job log.
- **Watch**: the dashboard. Token is `DASHBOARD_TOKEN` in the server's `.env`
  (`/home/loewenmut-coinbase/htdocs/coinbase.loewenmut.ch/.env`). One-time link: `https://coinbase.loewenmut.ch/?token=...`.
- **Secrets** live in GitHub → Settings → Secrets: `VPS_HOST`, `VPS_USER` (`ubuntu`), `VPS_PORT`, `VPS_SSH_KEY`.
  SSH key pair for the deploy is on the previous developer's machine; generate a new one if lost
  (`deploy/README.md` step 1–3).

## 4. Local development

```bash
git clone https://github.com/hemantcts/coinbase && cd coinbase
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
set DATA_DIR=./data                             # or export on Linux/macOS
pytest -q                                       # 52 tests, must stay green
python -m trader import-history                 # two years of candles from data/history
python -m trader backtest --strategy trendhold --profile all --days 730 --granularity 21600
python -m trader backtest --strategy trendhold --profile medium --days 730 --granularity 21600 --walk-forward
python -m trader paper                          # paper trade locally (Ctrl-C to stop)
python -m trader dashboard                      # http://127.0.0.1:8080
```

## 5. Rules that must not change without the client

- `LIVE_TRADING` stays `false` until phase 4 is approved. Going live needs all of: `LIVE_TRADING=true`,
  the file `data/LIVE_ARMED` with the exact sentence from `trader/execution/live.py`, a passed
  `gate_result.json` younger than 7 days, and an API key that can trade but not transfer. The deploy
  refuses to run while `LIVE_TRADING=true`; live is a manual step on the server with a separate `DB_PATH`.
- Never commit `.env`, keys, or anything under `data/` except `data/history/`.
- Never loosen the risk controller (`trader/risk.py`) or the gate (`trader/gate.py`) to make a test pass.
- Any strategy or parameter change must be justified with the two-year backtest **and** walk-forward,
  and compared against buy-and-hold. The research report shows how quickly a four-month result fooled us.
- Keep deploys few during the paper phase: each deploy restarts the engine (state is restored, but the
  gate counts gaps, max 30 minutes).

## 6. Milestones ahead

1. **Sunday 19 Oct**: run `gate`; Phase B should turn green (≥ 72 h, no gaps, no errors).
2. **~15 Nov**: run `gate` and `status`, review fills and P&L on the dashboard, write the client a short
   summary (orders, equity curve, any halts). Client decides on phase 4.
3. **Phase 4 prep**: client creates a Coinbase portfolio for the bot and an API key with view + trade
   only. `pip install coinbase-advanced-py` on the server (add to requirements). Put the key in the
   server's `.env`. Run `trader verify-live` (Server command: add an action for it, or SSH). It prints
   the real fee tier; if cheaper than 0.6/1.2, update `FEE_RATE`/`TAKER_FEE_RATE` and re-run the gate.
4. **Go live**: separate `DB_PATH=/data/live.sqlite3`, `LIVE_TRADING=true`, write `data/LIVE_ARMED`,
   change the compose command from `paper` to `live`, `docker compose up -d`. Pilot caps (10 % / 20 %)
   apply automatically. Watch the first fills on the dashboard. The live broker has never been run
   against the real Coinbase API; expect to debug the first order (`trader/execution/live.py`).

## 7. Working with Claude or ChatGPT on this code

They did the bulk of this build and can continue it. What works:

- Start every session by pasting or attaching `README.md`, `docs/RESEARCH-2026-10.md` and this file,
  and say which phase we are in. Then ask for one concrete change at a time.
- Ask for the change **with tests** and ask it to run `pytest -q` and `pyflakes trader tests` before
  showing the diff. Reject anything that skips or weakens a test.
- For strategy ideas: "Add strategy X to `trader/strategies/`, register it in `ALL`, then run the
  two-year backtest and walk-forward against trendhold and buy-and-hold, and show the table." If it
  cannot show a table from real data, it is guessing.
- For the live broker: give it the Coinbase Advanced Trade API docs link and the error text verbatim.
- For the dashboard: it is one file, `trader/dashboard/index.html`, with a translation table `I18N` at
  the top. German strings go there.
- Claude Code (claude.ai/code) with the GitHub repo connected can push to `main`, trigger the
  "Server command" workflow and read its logs, so it can operate the server without SSH. ChatGPT can
  edit code but cannot deploy; you push.
- Do not let an assistant change `.env` on the server, the secrets, or anything in section 5.

## 8. Known gaps and ideas, in priority order

1. Live broker untested against the real API (section 6, step 4).
2. `verify-live` as a "Server command" action.
3. Notifications: `NOTIFY_WEBHOOK` in `.env` posts fills and halts to any JSON webhook (Slack, Discord,
   n8n). Not configured yet.
4. Daily backup of `data/trader.sqlite3` off the server.
5. Later product idea (client): multi-user rental with low / medium / high profiles. The profiles and
   the per-user risk controller already exist; a multi-tenant version needs PostgreSQL, per-user keys
   and a billing front end (PHP/Laravel is fine; the Python engine stays as is and exposes the SQLite
   or a small API).
