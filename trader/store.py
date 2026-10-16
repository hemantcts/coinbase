"""SQLite persistence. One file, WAL mode, every state change in a transaction.

Tables
  meta        key/value (schema version, start capital, fx at start)
  candles     cached market data, also the backtest source
  orders      every order ever submitted, keyed by client order id
  fills       one row per fill (paper or live)
  state       the full portfolio state as JSON, one row, replaced atomically
  snapshots   equity curve, one row per cycle
  events      log of signals, halts, errors
  processed   (product, candle_ts) pairs already evaluated: restart-safe idempotency
  fx_rates    daily FX rates with the timestamp they were fetched
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import Candle, Order, OrderStatus, PortfolioState, Side

SCHEMA_VERSION = 2

_DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candles (
    product TEXT NOT NULL, granularity INTEGER NOT NULL, ts INTEGER NOT NULL,
    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
    PRIMARY KEY (product, granularity, ts));
CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY, product TEXT NOT NULL, side TEXT NOT NULL, qty REAL NOT NULL,
    price REAL NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    filled_qty REAL NOT NULL DEFAULT 0, avg_fill_price REAL NOT NULL DEFAULT 0, fee REAL NOT NULL DEFAULT 0,
    strategy TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'paper',
    exchange_order_id TEXT NOT NULL DEFAULT '', stop_price REAL NOT NULL DEFAULT 0, take_profit_price REAL NOT NULL DEFAULT 0, pnl REAL);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK (id = 1), updated_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots (
    ts TEXT PRIMARY KEY, cash REAL NOT NULL, equity REAL NOT NULL, exposure REAL NOT NULL,
    realised_pnl REAL NOT NULL, fees_paid REAL NOT NULL, halted_daily INTEGER NOT NULL, halted_total INTEGER NOT NULL,
    fx_rate REAL, report_equity REAL);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS processed (product TEXT NOT NULL, candle_ts INTEGER NOT NULL, PRIMARY KEY (product, candle_ts));
CREATE TABLE IF NOT EXISTS fx_rates (day TEXT NOT NULL, base TEXT NOT NULL, quote TEXT NOT NULL, rate REAL NOT NULL,
    fetched_at TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY (day, base, quote));
"""


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(_DDL)
        self.set_meta("schema_version", str(SCHEMA_VERSION))

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    # ---- meta ---------------------------------------------------------------
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (key, value))

    # ---- candles ------------------------------------------------------------
    def save_candles(self, product: str, granularity: int, candles: list[Candle]) -> int:
        if not candles:
            return 0
        with self.tx() as db:
            cur = db.executemany(
                "INSERT OR REPLACE INTO candles(product,granularity,ts,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?,?)",
                [(product, granularity, c.ts, c.open, c.high, c.low, c.close, c.volume) for c in candles])
        return cur.rowcount

    def candles(self, product: str, granularity: int, start_ts: int | None = None, end_ts: int | None = None,
                limit: int | None = None, newest_first: bool = False) -> list[Candle]:
        sql = "SELECT ts,open,high,low,close,volume FROM candles WHERE product=? AND granularity=?"
        args: list[Any] = [product, granularity]
        if start_ts is not None:
            sql += " AND ts>=?"
            args.append(start_ts)
        if end_ts is not None:
            sql += " AND ts<=?"
            args.append(end_ts)
        sql += " ORDER BY ts " + ("DESC" if newest_first else "ASC")
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        rows = self.db.execute(sql, args).fetchall()
        out = [Candle(r["ts"], r["open"], r["high"], r["low"], r["close"], r["volume"]) for r in rows]
        return list(reversed(out)) if newest_first else out

    def candle_range(self, product: str, granularity: int) -> tuple[int | None, int | None, int]:
        r = self.db.execute("SELECT MIN(ts) a, MAX(ts) b, COUNT(*) n FROM candles WHERE product=? AND granularity=?",
                            (product, granularity)).fetchone()
        return r["a"], r["b"], r["n"]

    # ---- orders & fills -------------------------------------------------------
    def save_order(self, order: Order, pnl: float | None = None) -> None:
        d = order.to_dict()
        with self.tx() as db:
            db.execute("""INSERT INTO orders(client_order_id,product,side,qty,price,status,created_at,updated_at,
                filled_qty,avg_fill_price,fee,strategy,reason,mode,exchange_order_id,stop_price,take_profit_price,pnl)
                VALUES (:client_order_id,:product,:side,:qty,:price,:status,:created_at,:updated_at,
                :filled_qty,:avg_fill_price,:fee,:strategy,:reason,:mode,:exchange_order_id,:stop_price,:take_profit_price,:pnl)
                ON CONFLICT(client_order_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at,
                filled_qty=excluded.filled_qty, avg_fill_price=excluded.avg_fill_price, fee=excluded.fee,
                exchange_order_id=excluded.exchange_order_id, pnl=COALESCE(excluded.pnl, orders.pnl)""",
                       {**d, "pnl": pnl})

    def get_order(self, client_order_id: str) -> Order | None:
        r = self.db.execute("SELECT * FROM orders WHERE client_order_id=?", (client_order_id,)).fetchone()
        return _row_to_order(r) if r else None

    def orders(self, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM orders"
        args: list[Any] = []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def open_orders(self) -> list[Order]:
        rows = self.db.execute("SELECT * FROM orders WHERE status IN ('NEW','SUBMITTED','PARTIAL','UNKNOWN')").fetchall()
        return [_row_to_order(r) for r in rows]

    def fills(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM orders WHERE status='FILLED' ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---- state & snapshots ----------------------------------------------------
    def save_state(self, state: PortfolioState, ts: str, report_equity: float | None = None) -> None:
        d = state.to_dict()
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO state(id,updated_at,data) VALUES (1,?,?)", (ts, json.dumps(d)))
            db.execute("""INSERT OR REPLACE INTO snapshots(ts,cash,equity,exposure,realised_pnl,fees_paid,halted_daily,halted_total,fx_rate,report_equity)
                          VALUES (?,?,?,?,?,?,?,?,?,?)""",
                       (ts, d["cash"], d["equity"], d["exposure"], d["realised_pnl"], d["fees_paid"],
                        int(d["halted_daily"]), int(d["halted_total"]), d["fx_rate"], report_equity))

    def load_state(self) -> PortfolioState | None:
        r = self.db.execute("SELECT data FROM state WHERE id=1").fetchone()
        return PortfolioState.from_dict(json.loads(r["data"])) if r else None

    def snapshots(self, limit: int = 2000, since: str | None = None) -> list[dict[str, Any]]:
        if since:
            rows = self.db.execute("SELECT * FROM snapshots WHERE ts>=? ORDER BY ts ASC", (since,)).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM (SELECT * FROM snapshots ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---- events ---------------------------------------------------------------
    def log(self, ts: str, kind: str, detail: Any) -> None:
        if not isinstance(detail, str):
            detail = json.dumps(detail, default=str)
        self.db.execute("INSERT INTO events(ts,kind,detail) VALUES (?,?,?)", (ts, kind, detail))

    def events(self, limit: int = 200, kind: str | None = None, since: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM events"
        where, args = [], []
        if kind:
            where.append("kind=?"); args.append(kind)
        if since:
            where.append("ts>=?"); args.append(since)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def count_events(self, kind: str, since: str | None = None) -> int:
        if since:
            return self.db.execute("SELECT COUNT(*) FROM events WHERE kind=? AND ts>=?", (kind, since)).fetchone()[0]
        return self.db.execute("SELECT COUNT(*) FROM events WHERE kind=?", (kind,)).fetchone()[0]

    # ---- idempotency ------------------------------------------------------------
    def is_processed(self, product: str, candle_ts: int) -> bool:
        return self.db.execute("SELECT 1 FROM processed WHERE product=? AND candle_ts=?", (product, candle_ts)).fetchone() is not None

    def mark_processed(self, product: str, candle_ts: int) -> None:
        self.db.execute("INSERT OR IGNORE INTO processed(product,candle_ts) VALUES (?,?)", (product, candle_ts))

    # ---- fx -----------------------------------------------------------------------
    def get_fx(self, day: str, base: str, quote: str) -> dict[str, Any] | None:
        r = self.db.execute("SELECT * FROM fx_rates WHERE day=? AND base=? AND quote=?", (day, base, quote)).fetchone()
        return dict(r) if r else None

    def save_fx(self, day: str, base: str, quote: str, rate: float, fetched_at: str, source: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO fx_rates(day,base,quote,rate,fetched_at,source) VALUES (?,?,?,?,?,?)",
                        (day, base, quote, rate, fetched_at, source))


def _row_to_order(r: sqlite3.Row) -> Order:
    return Order(
        client_order_id=r["client_order_id"], product=r["product"], side=Side(r["side"]), qty=r["qty"], price=r["price"],
        status=OrderStatus(r["status"]), created_at=r["created_at"], updated_at=r["updated_at"], filled_qty=r["filled_qty"],
        avg_fill_price=r["avg_fill_price"], fee=r["fee"], strategy=r["strategy"], reason=r["reason"], mode=r["mode"],
        exchange_order_id=r["exchange_order_id"], stop_price=r["stop_price"], take_profit_price=r["take_profit_price"],
    )
