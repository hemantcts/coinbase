import sqlite3
from pathlib import Path

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS snapshots (ts TEXT PRIMARY KEY, cash REAL NOT NULL, equity REAL NOT NULL, positions TEXT NOT NULL, day TEXT NOT NULL, halted INTEGER NOT NULL)")
    db.commit()
    return db

def log(db, ts, kind, detail):
    db.execute("INSERT INTO events(ts,kind,detail) VALUES (?,?,?)", (ts, kind, detail))
    db.commit()
