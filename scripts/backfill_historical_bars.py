#!/usr/bin/env python
"""Backfill historical OHLCV bars from Binance klines API."""
import argparse
import asyncio
import os
from datetime import datetime, timedelta, UTC
from decimal import Decimal

import httpx
from sqlalchemy.orm import sessionmaker

from loophedge.config import get_settings
from loophedge.db import get_session_factory
from loophedge.models import Bar


TIMEFRAME_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440,
}


async def fetch_klines_batch(
    base_url: str, symbol: str, timeframe: str, start_time: int, end_time: int
) -> list[dict]:
    """Fetch a batch of klines from Binance. Returns up to 1000 bars."""
    params = {
        "symbol": symbol,
        "interval": timeframe,
        "startTime": start_time,
        "endTime": end_time,
        "limit": 1000,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{base_url}/api/v3/klines", params=params)
        r.raise_for_status()
        return r.json()


async def backfill(
    symbol: str, timeframe: str, days: int, base_url: str = None
) -> None:
    """Fetch and store historical bars."""
    if base_url is None:
        base_url = os.environ.get("BINANCE_API_BASE", "https://api.binance.com").rstrip("/")

    settings = get_settings()
    session_factory = get_session_factory()

    minute_step = TIMEFRAME_MINUTES.get(timeframe)
    if not minute_step:
        raise ValueError(f"Unknown timeframe: {timeframe}")

    # Calculate time window: from `days` ago to now
    end_ts = datetime.now(UTC)
    start_ts = end_ts - timedelta(days=days)

    start_ms = int(start_ts.timestamp() * 1000)
    end_ms = int(end_ts.timestamp() * 1000)

    print(f"Backfilling {symbol} {timeframe} from {start_ts.isoformat()} to {end_ts.isoformat()}")
    print(f"Time window: {start_ms} to {end_ms}")

    inserted = 0
    skipped = 0
    current_ms = start_ms

    # Fetch in batches (Binance returns up to 1000 bars per request)
    while current_ms < end_ms:
        print(f"Fetching batch starting at {datetime.fromtimestamp(current_ms / 1000, tz=UTC).isoformat()}...", end=" ", flush=True)

        try:
            rows = await fetch_klines_batch(base_url, symbol, timeframe, current_ms, end_ms)
        except Exception as e:
            print(f"\nError fetching batch: {e}")
            break

        if not rows:
            print("(no more data)")
            break

        batch_inserted = 0
        for row in rows:
            ts = datetime.fromtimestamp(row[0] / 1000, tz=UTC)
            open_price = Decimal(row[1])
            high_price = Decimal(row[2])
            low_price = Decimal(row[3])
            close_price = Decimal(row[4])
            volume = Decimal(row[7])

            bar = Bar(
                symbol=symbol,
                timeframe=timeframe,
                ts=ts,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
            )

            with session_factory() as s:
                exists = s.get(Bar, (symbol, timeframe, ts))
                if exists:
                    skipped += 1
                else:
                    s.add(bar)
                    s.commit()
                    batch_inserted += 1
                    inserted += 1

        print(f"{batch_inserted} inserted, {len(rows)} total in batch")

        # Move to the end of this batch for the next request
        if rows:
            last_ts = rows[-1][0]
            current_ms = last_ts + (minute_step * 60 * 1000)

    print(f"\nDone. Inserted: {inserted}, Skipped (already existed): {skipped}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill historical OHLCV bars")
    parser.add_argument("--symbol", default="BTCUSDT", help="Symbol to backfill (default: BTCUSDT)")
    parser.add_argument("--timeframe", default="1h", help="Timeframe (default: 1h)")
    parser.add_argument("--days", type=int, default=365, help="Days to backfill (default: 365)")
    parser.add_argument("--base-url", help="Binance API base URL (default: from env or api.binance.com)")

    args = parser.parse_args()

    asyncio.run(backfill(args.symbol, args.timeframe, args.days, args.base_url))
