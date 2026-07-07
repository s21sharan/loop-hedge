import asyncio

import fakeredis.aioredis
import pytest

from loophedge.bus import Bus, CH_BAR_CLOSED
from loophedge.schemas import BarClosed


@pytest.mark.asyncio
async def test_publish_subscribe_roundtrip():
    redis = fakeredis.aioredis.FakeRedis()
    bus = Bus(redis)

    received = []

    async def consumer():
        async for msg in bus.subscribe(CH_BAR_CLOSED):
            received.append(msg)
            break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)  # let subscriber attach

    bar = BarClosed(symbol="BTCUSDT", timeframe="5m",
                    ts="2026-06-29T12:00:00+00:00",
                    open="60000", high="60100", low="59900",
                    close="60050", volume="12.5")
    await bus.publish(CH_BAR_CLOSED, bar)

    await asyncio.wait_for(task, timeout=1.0)
    assert received[0]["symbol"] == "BTCUSDT"
    assert received[0]["close"] == "60050"
