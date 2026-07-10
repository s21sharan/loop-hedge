from datetime import UTC, datetime, timedelta
from decimal import Decimal

import fakeredis.aioredis
import pytest

from loophedge.bus import Bus
from loophedge.models import EquitySnapshot, RiskEvent
from loophedge.services.risk_monitor import RiskMonitor


@pytest.mark.asyncio
async def test_no_kill_within_threshold(session_factory):
    rm = RiskMonitor(Bus(fakeredis.aioredis.FakeRedis()), session_factory,
                     kill_dd_pct=Decimal("0.15"))
    now = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    assert await rm.tick(now, Decimal("100000")) is None
    assert await rm.tick(now + timedelta(minutes=1), Decimal("95000")) is None  # 5% dd
    with session_factory() as s:
        assert s.query(EquitySnapshot).count() == 2


@pytest.mark.asyncio
async def test_kill_fires_on_threshold(session_factory):
    rm = RiskMonitor(Bus(fakeredis.aioredis.FakeRedis()), session_factory,
                     kill_dd_pct=Decimal("0.15"))
    now = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    assert await rm.tick(now, Decimal("100000")) is None
    event = await rm.tick(now + timedelta(minutes=1), Decimal("84500"))  # 15.5% dd
    assert event is not None
    assert event.action == "flatten_all"
    assert event.drawdown_pct == Decimal("0.155")
    with session_factory() as s:
        assert s.query(RiskEvent).count() == 1


@pytest.mark.asyncio
async def test_kill_fires_on_negative_equity_with_no_baseline(session_factory):
    """Catastrophic loss with no prior snapshots should still trip the kill switch."""
    rm = RiskMonitor(Bus(fakeredis.aioredis.FakeRedis()), session_factory,
                     kill_dd_pct=Decimal("0.15"))
    now = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    event = await rm.tick(now, Decimal("-50000"))
    assert event is not None
    assert event.drawdown_pct == Decimal("1")


@pytest.mark.asyncio
async def test_rolling_high_window_is_30_days(session_factory):
    rm = RiskMonitor(Bus(fakeredis.aioredis.FakeRedis()), session_factory,
                     kill_dd_pct=Decimal("0.15"))
    old = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    now = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    await rm.tick(old, Decimal("200000"))  # old high should NOT count
    assert await rm.tick(now, Decimal("100000")) is None  # 100k is the new high
    # 15% below new high = 85k → still no kill at 90k
    assert await rm.tick(now + timedelta(minutes=1), Decimal("90000")) is None
