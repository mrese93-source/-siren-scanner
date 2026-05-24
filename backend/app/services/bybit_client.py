import httpx
import asyncio
from typing import Optional


BYBIT_BASE = "https://api.bybit.com"


async def get_tickers() -> list[dict]:
    """Fetch all linear perpetual tickers from Bybit."""
    url = f"{BYBIT_BASE}/v5/market/tickers"
    params = {"category": "linear"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    return data.get("result", {}).get("list", [])


async def get_klines(symbol: str, interval: str = "1", limit: int = 60) -> list[dict]:
    """
    Fetch klines for a symbol.
    interval: "1" = 1m, "5" = 5m, "15" = 15m, "60" = 1h, "D" = 1d
    """
    url = f"{BYBIT_BASE}/v5/market/kline"
    params = {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    raw = data.get("result", {}).get("list", [])
    return [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
            "turnover": float(r[6]),
        }
        for r in raw
    ]


async def get_open_interest(symbol: str, interval: str = "1h", limit: int = 2) -> Optional[float]:
    """Fetch current open interest for a symbol."""
    url = f"{BYBIT_BASE}/v5/market/open-interest"
    params = {"category": "linear", "symbol": symbol, "intervalTime": interval, "limit": limit}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        items = data.get("result", {}).get("list", [])
        if items:
            return float(items[0].get("openInterest", 0))
    except Exception:
        pass
    return None


async def get_funding_rate(symbol: str) -> Optional[float]:
    """Fetch current funding rate for a symbol."""
    url = f"{BYBIT_BASE}/v5/market/funding/history"
    params = {"category": "linear", "symbol": symbol, "limit": 1}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        items = data.get("result", {}).get("list", [])
        if items:
            return float(items[0].get("fundingRate", 0))
    except Exception:
        pass
    return None


async def get_multi_timeframe_changes(symbol: str) -> dict:
    """Get price change % across 1m, 5m, 15m, 1h timeframes using klines."""
    results = {}
    tasks = [
        ("1m",  get_klines(symbol, "1",  2)),
        ("5m",  get_klines(symbol, "5",  2)),
        ("15m", get_klines(symbol, "15", 2)),
        ("1h",  get_klines(symbol, "60", 2)),
    ]
    gathered = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
    for i, (label, _) in enumerate(tasks):
        klines = gathered[i]
        if isinstance(klines, Exception) or len(klines) < 2:
            results[label] = 0.0
        else:
            prev = klines[-1]["close"]  # older candle (Bybit returns newest first)
            curr = klines[0]["close"]
            results[label] = round((curr - prev) / prev * 100, 4) if prev else 0.0
    return results
