from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from loophedge.models import Bar, Base, Contract
from loophedge.services.kalshi_ingestor import KalshiIngestor


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_sync_contracts_inserts_new_kalshi_markets(session_factory):
    fake_markets = [{
        "ticker": "KXHIGHNY-26AUG05-B82.5",
        "event_ticker": "KXHIGHNY-26AUG05",
        "status": "open",
        "open_time": "2026-08-04T12:00:00Z",
        "close_time": "2026-08-05T22:00:00Z",
        "expiration_time": "2026-08-05T22:00:00Z",
        "subtitle": "82.5-84.5°F",
    }]
    ing = KalshiIngestor(
        session_factory,
        fetch_markets=AsyncMock(return_value=fake_markets),
        fetch_candles=AsyncMock(return_value=[]),
        fetch_settlement=AsyncMock(return_value={"settled": False,
                                                  "settlement_value": None}),
        cities=["NYC"],
    )
    n = await ing.sync_contracts_once()
    assert n >= 1
    with session_factory() as s:
        c = s.get(Contract, "KXHIGHNY-26AUG05-B82.5")
    assert c is not None
    assert c.venue == "kalshi"
    assert c.contract_metadata.get("city") == "NYC"


@pytest.mark.asyncio
async def test_sync_contracts_is_idempotent(session_factory):
    fake_markets = [{
        "ticker": "KXHIGHNY-26AUG05-B82.5",
        "event_ticker": "KXHIGHNY-26AUG05",
        "status": "open",
        "close_time": "2026-08-05T22:00:00Z",
        "expiration_time": "2026-08-05T22:00:00Z",
    }]
    ing = KalshiIngestor(
        session_factory,
        fetch_markets=AsyncMock(return_value=fake_markets),
        fetch_candles=AsyncMock(return_value=[]),
        fetch_settlement=AsyncMock(return_value={"settled": False,
                                                  "settlement_value": None}),
        cities=["NYC"],
    )
    await ing.sync_contracts_once()
    await ing.sync_contracts_once()  # second call must not raise
    with session_factory() as s:
        contracts = list(s.execute(select(Contract)).scalars())
    assert len(contracts) == 1


@pytest.mark.asyncio
async def test_settlement_write_creates_final_bar(session_factory):
    # Contract exists but not yet settled in DB
    with session_factory() as s:
        s.add(Contract(symbol="KXHIGHNY-26AUG05-B82.5", venue="kalshi",
                       resolution_ts=datetime(2026, 8, 5, 22, 0, tzinfo=UTC)))
        s.commit()

    ing = KalshiIngestor(
        session_factory,
        fetch_markets=AsyncMock(return_value=[]),
        fetch_candles=AsyncMock(return_value=[]),
        fetch_settlement=AsyncMock(return_value={"settled": True,
                                                  "settlement_value": Decimal("1")}),
        cities=["NYC"],
    )
    n = await ing.sync_contracts_once()
    with session_factory() as s:
        c = s.get(Contract, "KXHIGHNY-26AUG05-B82.5")
        assert c.settlement_value == Decimal("1")
        # Also expects a Bar with close=1 at resolution_ts
        bar = s.get(Bar, ("KXHIGHNY-26AUG05-B82.5", "5m",
                          datetime(2026, 8, 5, 22, 0, tzinfo=UTC)))
        assert bar is not None
        assert bar.close == Decimal("1")


@pytest.mark.asyncio
async def test_fetch_candles_writes_bars_for_active_contracts(session_factory):
    with session_factory() as s:
        s.add(Contract(symbol="KXHIGHNY-26AUG05-B82.5", venue="kalshi",
                       resolution_ts=datetime(2026, 8, 5, 22, 0, tzinfo=UTC)))
        s.commit()

    fake_candles = [{
        "ts": 1754433900,  # 2026-08-05 21:25 UTC
        "open": Decimal("0.42"), "high": Decimal("0.47"),
        "low": Decimal("0.40"),  "close": Decimal("0.45"),
        "volume": Decimal("1000"),
    }]
    ing = KalshiIngestor(
        session_factory,
        fetch_markets=AsyncMock(return_value=[]),
        fetch_candles=AsyncMock(return_value=fake_candles),
        fetch_settlement=AsyncMock(return_value={"settled": False,
                                                  "settlement_value": None}),
        cities=["NYC"],
    )
    n = await ing.fetch_candles_once()
    assert n == 1
    with session_factory() as s:
        bars = list(s.execute(select(Bar).where(
            Bar.symbol == "KXHIGHNY-26AUG05-B82.5")).scalars())
    assert len(bars) == 1
    assert bars[0].close == Decimal("0.45")
