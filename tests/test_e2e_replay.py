from datetime import UTC, datetime, timedelta
from decimal import Decimal

import fakeredis.aioredis
import pytest

from loophedge.bus import Bus
from loophedge.ledger.simulator import Simulator
from loophedge.models import Bar, Fill, Position, Signal
from loophedge.schemas import SignalCandidate, SignalVerified
from loophedge.services.data_ingestor import DataIngestor
from loophedge.services.executor import Executor
from loophedge.services.risk_monitor import RiskMonitor


@pytest.mark.asyncio
async def test_full_replay_pipeline(session_factory, starting_capital):
    """30 bars, one buy at bar 5, one sell at bar 25, drawdown injected at bar 28."""
    base_ts = 1_700_000_000_000  # ms
    rows = [
        {"open_time": base_ts + i * 300_000,
         "open": str(60000 + i * 100), "high": str(60100 + i * 100),
         "low": str(59900 + i * 100), "close": str(60050 + i * 100),
         "volume": "1.0"}
        for i in range(30)
    ]

    async def fake_klines(*_):
        return rows

    bus = Bus(fakeredis.aioredis.FakeRedis())
    ing = DataIngestor(bus, session_factory, fake_klines, ["BTCUSDT"], "5m")
    assert await ing.fetch_and_publish_once() == 30

    sim = Simulator(starting_cash=starting_capital)
    latest_prices = {"BTCUSDT": Decimal("60500")}  # mid of bar 5
    ex = Executor(bus, session_factory, sim, latest_prices)

    with session_factory() as s:
        s.add(Signal(id="buy1", strategy_id="momentum_btc", symbol="BTCUSDT",
                     side="long", size_pct=Decimal("0.02"), status="approved",
                     maker_payload={}))
        s.commit()

    fill = await ex.handle_verified(
        SignalVerified(signal_id="buy1", verdict="approve"),
        SignalCandidate(signal_id="buy1", strategy_id="momentum_btc",
                         symbol="BTCUSDT", side="long",
                         size_pct=Decimal("0.02"), reasoning="entry"),
    )
    assert fill is not None
    with session_factory() as s:
        assert s.query(Fill).count() == 1
        assert s.get(Position, "BTCUSDT").qty > 0

    rm = RiskMonitor(bus, session_factory, kill_dd_pct=Decimal("0.15"))
    now = datetime.now(UTC)
    await rm.tick(now, starting_capital)                   # baseline
    event = await rm.tick(now + timedelta(minutes=1),
                           starting_capital * Decimal("0.80"))  # 20% dd
    assert event is not None
    assert event.action == "flatten_all"

    with session_factory() as s:
        bar_count = s.query(Bar).count()
        sig_count = s.query(Signal).filter(Signal.status == "executed").count()
        pos = s.get(Position, "BTCUSDT")
    assert bar_count == 30
    assert sig_count == 1
    assert pos.qty > 0
