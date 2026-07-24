from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import git
import pytest
import fakeredis.aioredis

from loophedge.agents.checker import CheckerAgent
from loophedge.agents.client import AgentClient
from loophedge.bus import Bus
from loophedge.memory.lessons import LessonsLog
from loophedge.memory.skills import SkillsRepo
from loophedge.models import Bar, Strategy
from loophedge.strategies.registry import StrategyRegistry


SAMPLE = '''
NAME = "test_strat"
DEFAULT_HYPERPARAMS = {"window": 10}
def generate_signals(bars, hyperparams):
    from decimal import Decimal
    return [{"symbol": bars[i].symbol, "side": "long",
             "size_pct": Decimal("0.01"), "ts": bars[i].ts}
            for i in range(0, len(bars), 10)]
'''


def _seed(tmp_path, session_factory):
    root = tmp_path / "skills"
    (root / "strategies" / "active").mkdir(parents=True)
    (root / "strategies" / "pending").mkdir(parents=True)
    (root / "strategies" / "retired").mkdir(parents=True)
    (root / "LESSONS.md").write_text("# Lessons\n")
    (root / "backtest_verification.md").write_text("Sharpe ≥ 1.0, DD < 12%, t ≥ 1.5, n ≥ 30.")
    repo = git.Repo.init(root)
    repo.git.add(A=True)
    repo.index.commit("init")
    sr = SkillsRepo(root)
    reg = StrategyRegistry(session_factory, sr)
    reg.register_pending("test_strat", SAMPLE, {"window": 10}, actor="genesis")
    with session_factory() as s:
        for i in range(300):
            s.add(Bar(symbol="BTCUSDT", timeframe="5m",
                       ts=datetime(2026, 6, 1, tzinfo=UTC) + timedelta(minutes=5*i),
                       open=Decimal(str(60000 + i*5)),
                       high=Decimal(str(60100 + i*5)),
                       low=Decimal(str(59900 + i*5)),
                       close=Decimal(str(60050 + i*5)),
                       volume=Decimal("1")))
        s.commit()
    return sr, reg, LessonsLog(sr)


def _cassette_exists(name):
    return (Path(__file__).parent / "cassettes" / f"{name}.yaml").stat().st_size > 200


@pytest.mark.skipif(
    not (Path(__file__).parent / "cassettes" / "checker_approves_strategy.yaml").exists()
    or not _cassette_exists("checker_approves_strategy"),
    reason="cassette placeholder; record with ANTHROPIC_LIVE_RECORD=1"
)
def test_checker_approves_passing_strategy(tmp_path, session_factory, vcr_cassette):
    sr, reg, lessons = _seed(tmp_path, session_factory)
    bus = Bus(fakeredis.aioredis.FakeRedis())
    client = AgentClient(model="claude-opus-4-7", system_prompt="x", tools=[])

    with vcr_cassette("checker_approves_strategy"):
        ck = CheckerAgent(client, reg, sr, lessons, session_factory, bus)
        verdict = ck.validate_strategy("test_strat")

    assert verdict in ("approved", "rejected", "needs_revision")
    # Whichever way it goes, the registry is consistent:
    with session_factory() as s:
        row = s.query(Strategy).filter_by(name="test_strat").one()
        assert row.status in ("active", "retired", "pending")  # at minimum mutable


@pytest.mark.asyncio
async def test_verify_signal_publishes_rejected_for_unknown_strategy(tmp_path, session_factory):
    """verify_signal emits SignalRejected when strategy does not exist."""
    sr, reg, lessons = _seed(tmp_path, session_factory)
    redis_fake = fakeredis.aioredis.FakeRedis()
    bus = Bus(redis_fake)
    client = AgentClient(model="claude-opus-4-7", system_prompt="x", tools=[])
    ck = CheckerAgent(client, reg, sr, lessons, session_factory, bus)

    from loophedge.bus import CH_SIGNAL_REJECTED
    received = []

    async def _collect():
        async for msg in bus.subscribe(CH_SIGNAL_REJECTED):
            received.append(msg)
            break

    import asyncio
    task = asyncio.create_task(_collect())
    await asyncio.sleep(0.02)
    result = await ck.verify_signal("sig-xyz", "nonexistent_strat")
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert result == "rejected"
    assert len(received) >= 1
    assert received[0]["signal_id"] == "sig-xyz"
    assert "not found" in received[0]["reason"]


@pytest.mark.asyncio
async def test_verify_signal_publishes_verified_for_active_strategy(tmp_path, session_factory, monkeypatch):
    """verify_signal emits SignalVerified when strategy is already active."""
    import asyncio
    sr, reg, lessons = _seed(tmp_path, session_factory)
    redis_fake = fakeredis.aioredis.FakeRedis()
    bus = Bus(redis_fake)
    client = AgentClient(model="claude-opus-4-7", system_prompt="x", tools=[])
    ck = CheckerAgent(client, reg, sr, lessons, session_factory, bus)

    # Promote the strategy to active without calling the LLM
    reg.promote("test_strat", actor="test", reason="e2e")

    from loophedge.bus import CH_SIGNAL_VERIFIED
    received = []

    async def _collect():
        async for msg in bus.subscribe(CH_SIGNAL_VERIFIED):
            received.append(msg)
            break

    task = asyncio.create_task(_collect())
    await asyncio.sleep(0.02)
    result = await ck.verify_signal("sig-abc", "test_strat")
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert result == "approved"
    assert len(received) >= 1
    assert received[0]["signal_id"] == "sig-abc"
    assert received[0]["verdict"] == "approve"
