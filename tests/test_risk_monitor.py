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


def test_compute_equity_counts_realized_losses(session_factory):
    """A closed losing round trip must reduce equity, not vanish from it."""
    from loophedge.models import Fill
    from loophedge.services.risk_monitor import compute_equity

    now = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    with session_factory() as s:
        # Buy 1 @ 60000, sell 1 @ 50000: a realized 10k loss, flat afterwards.
        s.add(Fill(id="f1", signal_id=None, ts=now, symbol="BTCUSDT", side="long",
                   qty=Decimal("1"), price=Decimal("60000"), fees=Decimal("0"),
                   venue="simulator"))
        s.add(Fill(id="f2", signal_id=None, ts=now, symbol="BTCUSDT", side="short",
                   qty=Decimal("1"), price=Decimal("50000"), fees=Decimal("0"),
                   venue="simulator"))
        s.commit()
        assert compute_equity(s, Decimal("100000")) == Decimal("90000")


def test_compute_equity_counts_fees(session_factory):
    from loophedge.models import Fill
    from loophedge.services.risk_monitor import compute_equity

    now = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    with session_factory() as s:
        s.add(Fill(id="f1", signal_id=None, ts=now, symbol="BTCUSDT", side="long",
                   qty=Decimal("1"), price=Decimal("60000"), fees=Decimal("60"),
                   venue="simulator"))
        s.add(Fill(id="f2", signal_id=None, ts=now, symbol="BTCUSDT", side="short",
                   qty=Decimal("1"), price=Decimal("60000"), fees=Decimal("60"),
                   venue="simulator"))
        s.commit()
        assert compute_equity(s, Decimal("100000")) == Decimal("99880")


def test_compute_equity_marks_open_position_to_last_bar(session_factory):
    from loophedge.models import Bar, Fill, Position
    from loophedge.services.risk_monitor import compute_equity

    now = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    with session_factory() as s:
        s.add(Fill(id="f1", signal_id=None, ts=now, symbol="BTCUSDT", side="long",
                   qty=Decimal("1"), price=Decimal("60000"), fees=Decimal("0"),
                   venue="simulator"))
        s.add(Position(symbol="BTCUSDT", qty=Decimal("1"),
                       avg_entry=Decimal("60000"), unrealized_pnl=Decimal("0"),
                       updated_at=now))
        s.add(Bar(symbol="BTCUSDT", timeframe="5m", ts=now,
                  open=Decimal("65000"), high=Decimal("65000"),
                  low=Decimal("65000"), close=Decimal("65000"),
                  volume=Decimal("1")))
        s.commit()
        # 100k - 60k spent + 65k marked = 105k
        assert compute_equity(s, Decimal("100000")) == Decimal("105000")
