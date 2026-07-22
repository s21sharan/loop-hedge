import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import fakeredis.aioredis
import pytest

from loophedge.bus import CH_CIRCUIT_BROKEN, CH_SIGNAL_VERIFIED, Bus
from loophedge.ledger.simulator import Simulator
from loophedge.models import Position, Signal
from loophedge.schemas import CircuitBroken, SignalCandidate, SignalVerified
from loophedge.services.executor import Executor, ExecutorService


@pytest.mark.asyncio
async def test_executor_service_processes_signal_verified(session_factory, starting_capital):
    bus = Bus(fakeredis.aioredis.FakeRedis())
    sim = Simulator(starting_cash=starting_capital)
    ex = Executor(bus, session_factory, sim, latest_prices={"BTCUSDT": Decimal("60000")})
    svc = ExecutorService(ex, bus, sim, session_factory)

    # Pre-create candidate in DB
    with session_factory() as s:
        s.add(Signal(id="sigA", strategy_id="momentum", symbol="BTCUSDT",
                     side="long", size_pct=Decimal("0.02"), status="candidate",
                     maker_payload={"symbol": "BTCUSDT", "side": "long",
                                    "size_pct": "0.02", "reasoning": "t"}))
        s.commit()

    # Approve it
    with session_factory() as s:
        sig = s.get(Signal, "sigA")
        sig.status = "approved"
        s.commit()

    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.05)
    await bus.publish(CH_SIGNAL_VERIFIED, SignalVerified(signal_id="sigA", verdict="approve"))
    await asyncio.sleep(0.2)
    await svc.stop()
    await asyncio.wait_for(task, timeout=1.0)

    with session_factory() as s:
        assert s.get(Signal, "sigA").status == "executed"


@pytest.mark.asyncio
async def test_circuit_broken_flattens_positions(session_factory, starting_capital):
    bus = Bus(fakeredis.aioredis.FakeRedis())
    sim = Simulator(starting_cash=starting_capital)
    sim.apply_fill("BTCUSDT", "long", Decimal("0.1"), Decimal("60000"), datetime.now(UTC))
    with session_factory() as s:
        s.add(Position(symbol="BTCUSDT", qty=Decimal("0.1"),
                       avg_entry=Decimal("60030"), unrealized_pnl=Decimal("0"),
                       updated_at=datetime.now(UTC)))
        s.commit()
    ex = Executor(bus, session_factory, sim, latest_prices={"BTCUSDT": Decimal("60000")})
    svc = ExecutorService(ex, bus, sim, session_factory)

    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.05)
    await bus.publish(CH_CIRCUIT_BROKEN, CircuitBroken(
        ts=datetime.now(UTC), drawdown_pct=Decimal("0.2"), action="flatten_all"
    ))
    await asyncio.sleep(0.3)
    await svc.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert sim.positions["BTCUSDT"].qty == Decimal("0")
    with session_factory() as s:
        assert s.get(Position, "BTCUSDT").qty == Decimal("0")
