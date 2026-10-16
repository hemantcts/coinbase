import httpx
from datetime import datetime, timezone

BASE = "https://api.exchange.coinbase.com"

def candles(product: str, granularity: int = 300, limit: int = 300):
    if granularity not in (60, 300, 900, 3600, 21600, 86400):
        raise ValueError("Unsupported granularity")
    with httpx.Client(timeout=15, headers={"User-Agent": "PaperTradingResearch/0.1"}) as client:
        response = client.get(f"{BASE}/products/{product}/candles", params={"granularity": granularity})
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, list):
        raise RuntimeError("Unexpected Coinbase response")
    rows = sorted((r for r in data if isinstance(r, list) and len(r) >= 6), key=lambda r: r[0])
    now = datetime.now(timezone.utc).timestamp()
    # Exclude current unfinished candle, which can change after signal generation.
    return [(int(r[0]), float(r[4])) for r in rows if int(r[0]) + granularity <= now][-limit:]
