"""Kalshi ingester: hourly contract sync + 5-min candle polling.

The two schedules are independent. sync_contracts_once() should be called
hourly by the run loop; fetch_candles_once() should be called every 5 minutes.

All writes use session-scoped upserts (SQLAlchemy `merge` where the primary
key covers uniqueness) so re-running is safe.
"""
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from loophedge.models import Bar, Contract

FetchMarkets = Callable[[list[str]], Awaitable[list[dict]]]
FetchCandles = Callable[[str, int, int], Awaitable[list[dict]]]
FetchSettlement = Callable[[str], Awaitable[dict | None]]


def _extract_city_from_ticker(ticker: str) -> str | None:
    """KXHIGHNY-26AUG05-B82.5 -> NY (2-3 char code embedded in first segment)."""
    if not ticker.startswith("KXHIGH"):
        return None
    first_segment = ticker.split("-", 1)[0]
    return first_segment[len("KXHIGH"):]  # 'NY', 'LAX', ...


def _match_city(embedded: str, cities: list[str]) -> str | None:
    """Return the city from the list that corresponds to the embedded code.

    The embedded code (e.g. 'NY') may be a prefix of the city code (e.g. 'NYC').
    Returns the first matching city, or the embedded code if no match found.
    """
    for city in cities:
        if city.startswith(embedded):
            return city
    return embedded


def _parse_ts(iso: str | None) -> datetime | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


class KalshiIngestor:
    def __init__(
        self,
        session_factory: sessionmaker,
        fetch_markets: FetchMarkets,
        fetch_candles: FetchCandles,
        fetch_settlement: FetchSettlement,
        cities: list[str],
        timeframe: str = "5m",
        resolution_min: int = 5,
    ):
        self.session_factory = session_factory
        self.fetch_markets = fetch_markets
        self.fetch_candles = fetch_candles
        self.fetch_settlement = fetch_settlement
        self.cities = cities
        self.timeframe = timeframe
        self.resolution_min = resolution_min

    async def sync_contracts_once(self) -> int:
        """Insert new markets; update settlement values for existing contracts.

        Returns number of contract rows created or updated.
        """
        markets = await self.fetch_markets(self.cities)
        written = 0
        for m in markets:
            ticker = m["ticker"]
            embedded_city = _extract_city_from_ticker(ticker)
            city_code = _match_city(embedded_city, self.cities) if embedded_city else None
            with self.session_factory() as s:
                existing = s.get(Contract, ticker)
                if existing is None:
                    c = Contract(
                        symbol=ticker,
                        venue="kalshi",
                        open_ts=_parse_ts(m.get("open_time")),
                        close_ts=_parse_ts(m.get("close_time")),
                        resolution_ts=_parse_ts(m.get("expiration_time")),
                        resolution_source=m.get("event_ticker"),
                        contract_metadata={
                            "city": city_code,
                            "subtitle": m.get("subtitle", ""),
                        },
                    )
                    s.add(c)
                    s.commit()
                    written += 1

        # Second pass: check settlement for contracts whose settlement_value is
        # still null (regardless of resolution_ts).
        with self.session_factory() as s:
            unsettled = list(s.execute(
                select(Contract).where(Contract.venue == "kalshi",
                                       Contract.settlement_value.is_(None))
            ).scalars())
        for c in unsettled:
            try:
                info = await self.fetch_settlement(c.symbol)
            except Exception as e:
                print(f"[kalshi-ingestor] fetch_settlement({c.symbol}) failed: {e}",
                      flush=True)
                continue
            if info and info["settled"]:
                with self.session_factory() as s:
                    row = s.get(Contract, c.symbol)
                    row.settlement_value = info["settlement_value"]
                    s.commit()
                    # Emit a settlement bar at resolution_ts if we have one
                    if row.resolution_ts is not None:
                        settlement_bar = Bar(
                            symbol=row.symbol,
                            timeframe=self.timeframe,
                            ts=row.resolution_ts,
                            open=info["settlement_value"],
                            high=info["settlement_value"],
                            low=info["settlement_value"],
                            close=info["settlement_value"],
                            volume=Decimal("0"),
                        )
                        existing_bar = s.get(Bar, (row.symbol, self.timeframe,
                                                   row.resolution_ts))
                        if existing_bar is None:
                            s.add(settlement_bar)
                            s.commit()
                written += 1
        return written

    async def fetch_candles_once(self) -> int:
        """Poll active Kalshi contracts and write new Bar rows."""
        with self.session_factory() as s:
            active = list(s.execute(
                select(Contract).where(Contract.venue == "kalshi",
                                       Contract.settlement_value.is_(None))
            ).scalars())
        written = 0
        for c in active:
            try:
                candles = await self.fetch_candles(c.symbol, self.resolution_min, 200)
            except Exception as e:
                print(f"[kalshi-ingestor] fetch_candles({c.symbol}) failed: {e}",
                      flush=True)
                continue
            for candle in candles:
                ts = datetime.fromtimestamp(candle["ts"], tz=UTC)
                with self.session_factory() as s:
                    existing = s.get(Bar, (c.symbol, self.timeframe, ts))
                    if existing is not None:
                        continue
                    s.add(Bar(
                        symbol=c.symbol, timeframe=self.timeframe, ts=ts,
                        open=candle["open"], high=candle["high"],
                        low=candle["low"], close=candle["close"],
                        volume=candle["volume"],
                    ))
                    s.commit()
                    written += 1
        return written
