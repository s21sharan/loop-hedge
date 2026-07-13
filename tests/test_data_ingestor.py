import asyncio

import fakeredis.aioredis
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from loophedge.bus import CH_BAR_CLOSED, Bus
from loophedge.models import Base
from loophedge.services.data_ingestor import DataIngestor


def _session_factory():
    e = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(e)
    return sessionmaker(bind=e, future=True), e


def _fake_klines_factory(rows):
    async def _f(symbol, timeframe, limit):
        return rows
    return _f


@pytest.mark.asyncio
async def test_persists_new_bars_and_publishes():
    sf, _ = _session_factory()
    redis = fakeredis.aioredis.FakeRedis()
    bus = Bus(redis)

    rows = [
        {"open_time": 1_700_000_000_000, "open": "60000", "high": "60100",
         "low": "59900", "close": "60050", "volume": "1.5"},
    ]
    ing = DataIngestor(bus, sf, _fake_klines_factory(rows),
                       symbols=["BTCUSDT"], timeframe="5m")

    received = []

    async def consumer():
        async for msg in bus.subscribe(CH_BAR_CLOSED):
            received.append(msg)
            break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)

    count = await ing.fetch_and_publish_once()
    assert count == 1

    await asyncio.wait_for(task, timeout=1.0)
    assert received[0]["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_dedupes_existing_bars():
    sf, _ = _session_factory()
    redis = fakeredis.aioredis.FakeRedis()
    bus = Bus(redis)
    rows = [
        {"open_time": 1_700_000_000_000, "open": "1", "high": "1",
         "low": "1", "close": "1", "volume": "1"},
    ]
    ing = DataIngestor(bus, sf, _fake_klines_factory(rows),
                       symbols=["BTCUSDT"], timeframe="5m")
    assert await ing.fetch_and_publish_once() == 1
    assert await ing.fetch_and_publish_once() == 0
