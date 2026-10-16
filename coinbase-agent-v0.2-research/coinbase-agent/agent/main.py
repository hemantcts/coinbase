import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone
from .core import Limits, Portfolio, execute_paper, signal
from .market import candles
from .store import connect, log


def save(db, portfolio, now):
    db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?)", (now.isoformat(), portfolio.cash, portfolio.equity(), json.dumps(portfolio.holdings), portfolio.day, int(portfolio.halted)))
    db.commit()


def run_paper():
    if os.getenv("LIVE_TRADING", "false").lower() != "false":
        raise RuntimeError("LIVE_TRADING must remain false. Live trading is not implemented.")
    symbols = os.getenv("PRODUCTS", "BTC-EUR,ETH-EUR").split(",")
    limits = Limits(fee_rate=float(os.getenv("FEE_RATE", "0.006")), slippage_rate=float(os.getenv("SLIPPAGE_RATE", "0.001")))
    capital = float(os.getenv("STARTING_CASH_EUR", "1000"))
    portfolio = Portfolio(cash=capital, initial=capital, day_start_equity=capital)
    db = connect(os.getenv("DB_PATH", "./data/trades.sqlite3"))
    # Prevent restarting a simulated account from silently resetting its balance.
    existing = db.execute("SELECT cash,equity,positions,day,halted FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()
    if existing:
        portfolio.cash = existing[0]
        portfolio.holdings = json.loads(existing[2])
        portfolio.day = existing[3]
        portfolio.halted = bool(existing[4])
        portfolio.day_start_equity = existing[1] if existing[3] != datetime.now(timezone.utc).date().isoformat() else existing[1]
    last_processed = {}
    print("PAPER ONLY. No Coinbase API keys or live order endpoints are used.", flush=True)
    while True:
        now = datetime.now(timezone.utc)
        try:
            batches = {s: candles(s) for s in symbols}
            # Mark all holdings to market before evaluating any orders.
            for symbol, rows in batches.items():
                if rows:
                    portfolio.last_prices[symbol] = rows[-1][1]
            portfolio.check_limits(limits, now)
            for symbol, rows in batches.items():
                if len(rows) < 50 or last_processed.get(symbol) == rows[-1][0]:
                    continue
                last_processed[symbol] = rows[-1][0]
                decision = signal([price for _, price in rows])
                trade = execute_paper(portfolio, symbol, decision, rows[-1][1], limits, now)
                log(db, now.isoformat(), "signal", json.dumps({"product":symbol,"decision":decision,"candle":rows[-1][0]}))
                if trade:
                    log(db, now.isoformat(), "paper_fill", json.dumps(trade))
                    print("PAPER FILL", trade, flush=True)
            save(db, portfolio, now)
            print(now.isoformat(), "equity", round(portfolio.equity(),2), "halted", portfolio.halted, flush=True)
        except Exception as exc:
            log(db, now.isoformat(), "error", repr(exc))
            print("Market-data error; skipping this cycle:", repr(exc), flush=True)
        time.sleep(max(60, int(os.getenv("POLL_SECONDS", "300"))))


def backtest(path, capital, fee, slippage):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not {"timestamp", "close"}.issubset(reader.fieldnames or []):
            raise ValueError("CSV needs timestamp and close columns")
        data = [(datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")), float(r["close"])) for r in reader]
    if any(data[i][0] >= data[i+1][0] for i in range(len(data)-1)):
        raise ValueError("CSV timestamps must be strictly increasing")
    limits = Limits(fee_rate=fee, slippage_rate=slippage)
    p = Portfolio(cash=capital, initial=capital, day_start_equity=capital)
    history = []
    trades = 0
    for idx, (dt, close) in enumerate(data):
        p.last_prices["ASSET-EUR"] = close
        trade = execute_paper(p, "ASSET-EUR", signal([x[1] for x in data[max(0,idx-299):idx+1]]), close, limits, dt)
        trades += int(trade is not None)
        history.append(p.equity())
    peak = capital
    max_drawdown = 0
    for equity in history:
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1 - equity/peak)
    print(json.dumps({"starting_eur":capital,"ending_eur":round(p.equity(),2),"return_pct":round(100*(p.equity()/capital-1),2),"max_drawdown_pct":round(100*max_drawdown,2),"trades":trades,"halted":p.halted}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("paper")
    bt = sub.add_parser("backtest")
    bt.add_argument("csv")
    bt.add_argument("--capital", type=float, default=1000)
    bt.add_argument("--fee", type=float, default=0.006)
    bt.add_argument("--slippage", type=float, default=0.001)
    args = parser.parse_args()
    if args.command == "paper": run_paper()
    else: backtest(args.csv, args.capital, args.fee, args.slippage)

if __name__ == "__main__": main()
