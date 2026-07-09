from datetime import UTC, datetime
from decimal import Decimal

import fakeredis.aioredis
import pytest

from loophedge.bus import Bus
from loophedge.ledger.simulator import Simulator
from loophedge.models import Fill, Signal
from loophedge.schemas import SignalCandidate, SignalVerified
from loophedge.services.executor import Executor


def _candidate(size_pct="0.02", strat="momentum_btc"):
    return SignalCandidate(signal_id="sig1", strategy_id=strat,
                            symbol="BTCUSDT", side="long",
                            size_pct=Decimal(size_pct),
                            reasoning="t")


@pytest.mark.asyncio
async def test_approved_signal_creates_fill(session_factory, starting_capital):
    with session_factory() as s:
        s.add(Signal(id="sig1", strategy_id="momentum_btc", symbol="BTCUSDT",
                     side="long", size_pct=Decimal("0.02"), status="approved",
                     maker_payload={}))
        s.commit()
    sim = Simulator(starting_cash=starting_capital)
    ex = Executor(Bus(fakeredis.aioredis.FakeRedis()), session_factory, sim,
                  latest_prices={"BTCUSDT": Decimal("60000")})
    fill = await ex.handle_verified(
        SignalVerified(signal_id="sig1", verdict="approve"),
        _candidate(),
    )
    assert fill is not None
    with session_factory() as s:
        assert s.query(Fill).count() == 1
        assert s.get(Signal, "sig1").status == "executed"


@pytest.mark.asyncio
async def test_oversized_signal_rejected_by_caps(session_factory, starting_capital):
    with session_factory() as s:
        s.add(Signal(id="sig1", strategy_id="m", symbol="BTCUSDT", side="long",
                     size_pct=Decimal("0.10"), status="approved", maker_payload={}))
        s.commit()
    sim = Simulator(starting_cash=starting_capital)
    ex = Executor(Bus(fakeredis.aioredis.FakeRedis()), session_factory, sim,
                  latest_prices={"BTCUSDT": Decimal("60000")})
    fill = await ex.handle_verified(
        SignalVerified(signal_id="sig1", verdict="approve"),
        _candidate(size_pct="0.10"),
    )
    assert fill is None
    with session_factory() as s:
        sig = s.get(Signal, "sig1")
        assert sig.status == "killed"
        assert "position size" in sig.rejection_reason.lower()
