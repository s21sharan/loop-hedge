"""Kalshi public API client for market data.

The public candles/markets endpoints do not require authentication.
Base URL is env-configurable so demo (`demo-api.kalshi.co`) can be swapped
in for local testing without touching code.
"""
import os
from decimal import Decimal

import httpx

DEFAULT_BASE = "https://api.elections.kalshi.com/trade-api/v2"


def _base_url() -> str:
    return os.environ.get("KALSHI_API_BASE", DEFAULT_BASE).rstrip("/")


async def fetch_weather_markets(cities: list[str]) -> list[dict]:
    """Return open Kalshi daily-high weather markets for the given cities.

    Cities are airport-style codes: NYC, LAX, ORD, DFW, MIA.
    Kalshi tickers embed a shorter city code after KXHIGH (e.g. NY for NYC,
    LAX for LAX). We match by checking that the ticker's embedded city code
    is a prefix of (or equal to) any requested city code.
    Ticker prefix pattern is KXHIGH<embedded_code>-... for daily highs.
    """
    url = f"{_base_url()}/markets"
    params = {"status": "open", "limit": 1000}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    out = []
    for m in data.get("markets", []):
        ticker = m.get("ticker", "")
        if not ticker.startswith("KXHIGH"):
            continue
        first_segment = ticker.split("-", 1)[0]
        embedded = first_segment[len("KXHIGH"):]  # e.g. 'NY', 'LAX'
        # Match if any requested city starts with the embedded code
        # (e.g. 'NYC' starts with 'NY', 'LAX' == 'LAX')
        if any(city.startswith(embedded) for city in cities):
            out.append(m)
    return out


async def fetch_candles(ticker: str, resolution_min: int = 5,
                        limit: int = 200) -> list[dict]:
    """Return recent candles for a Kalshi ticker, prices normalized to Decimal
    dollars in 0.00-1.00 range (Kalshi API returns integer cents 0-100)."""
    url = f"{_base_url()}/markets/{ticker}/candlesticks"
    params = {"period_interval": resolution_min, "limit": limit}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    out = []
    for c in data.get("candlesticks", []):
        price = c.get("price", {})
        out.append({
            "ts": c["end_period_ts"],  # unix seconds
            "open": Decimal(price.get("open", 0)) / Decimal("100"),
            "high": Decimal(price.get("high", 0)) / Decimal("100"),
            "low": Decimal(price.get("low", 0)) / Decimal("100"),
            "close": Decimal(price.get("close", 0)) / Decimal("100"),
            "volume": Decimal(c.get("volume", 0)),
        })
    return out


async def fetch_settlement(ticker: str) -> dict | None:
    """Return {'settled': bool, 'settlement_value': Decimal|None} for a ticker.

    settlement_value is 1 for 'yes', 0 for 'no', None if not yet resolved.
    """
    url = f"{_base_url()}/markets/{ticker}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
    market = data.get("market", data)
    result = market.get("result")
    if result == "yes":
        return {"settled": True, "settlement_value": Decimal("1")}
    if result == "no":
        return {"settled": True, "settlement_value": Decimal("0")}
    return {"settled": False, "settlement_value": None}
