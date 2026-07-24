from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from loophedge.bus import CH_BAR_CLOSED, Bus
from loophedge.models import Bar
from loophedge.schemas import BarClosed

FetchKlines = Callable[[str, str, int], Awaitable[list[dict]]]


class DataIngestor:
    def __init__(
        self,
        bus: Bus,
        session_factory: sessionmaker,
        fetch_klines: FetchKlines,
        symbols: list[str],
        timeframe: str,
        limit: int = 100,
    ):
        self.bus = bus
        self.session_factory = session_factory
        self.fetch_klines = fetch_klines
        self.symbols = symbols
        self.timeframe = timeframe
        self.limit = limit

    async def fetch_and_publish_once(self) -> int:
        inserted = 0
        for symbol in self.symbols:
            rows = await self.fetch_klines(symbol, self.timeframe, self.limit)
            for row in rows:
                ts = datetime.fromtimestamp(row["open_time"] / 1000, tz=UTC)
                open_price = Decimal(row["open"])
                high_price = Decimal(row["high"])
                low_price = Decimal(row["low"])
                close_price = Decimal(row["close"])
                volume = Decimal(row["volume"])

                bar = Bar(
                    symbol=symbol, timeframe=self.timeframe, ts=ts,
                    open=open_price, high=high_price,
                    low=low_price, close=close_price,
                    volume=volume,
                )
                with self.session_factory() as s:
                    exists = s.get(Bar, (symbol, self.timeframe, ts))
                    if exists:
                        continue
                    s.add(bar)
                    s.commit()
                await self.bus.publish(CH_BAR_CLOSED, BarClosed(
                    symbol=symbol, timeframe=self.timeframe, ts=ts,
                    open=open_price, high=high_price, low=low_price,
                    close=close_price, volume=volume,
                ))
                inserted += 1
        return inserted


async def binance_fetch_klines(symbol: str, timeframe: str, limit: int) -> list[dict]:
    """Live Binance kline fetcher. Base URL is env-configurable so hosts blocked
    from api.binance.com (e.g. US-region DO droplets) can point at
    testnet.binance.vision or api.binance.us instead."""
    import os

    import httpx
    base = os.environ.get("BINANCE_API_BASE", "https://api.binance.com").rstrip("/")
    url = f"{base}/api/v3/klines"
    params: dict[str, str | int] = {"symbol": symbol, "interval": timeframe, "limit": limit}
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    return [
        {"open_time": row[0], "open": row[1], "high": row[2],
         "low": row[3], "close": row[4], "volume": row[5]}
        for row in data
    ]
