import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import fakeredis.aioredis
import git
import pytest

from loophedge.agents.checker import CheckerAgent
from loophedge.agents.client import AgentClient
from loophedge.agents.genesis import GenesisAgent
from loophedge.agents.maker import MakerAgent
from loophedge.bus import Bus, CH_SIGNAL_VERIFIED
from loophedge.ledger.simulator import Simulator
from loophedge.memory.lessons import LessonsLog
from loophedge.memory.skills import SkillsRepo
from loophedge.models import Bar, Fill, Position, Signal, Strategy
from loophedge.schemas import SignalVerified
from loophedge.services.executor import Executor, ExecutorService
from loophedge.strategies.registry import StrategyRegistry


BAKED_STRATEGY = '''
NAME = "baked_sma"
DEFAULT_HYPERPARAMS = {"window": 5}
def generate_signals(bars, hyperparams):
    from decimal import Decimal
    if len(bars) < hyperparams["window"]:
        return []
    return [{"symbol": bars[-1].symbol, "side": "long",
             "size_pct": Decimal("0.01"), "ts": bars[-1].ts}]
'''


def _seed(tmp_path, session_factory):
    root = tmp_path / "skills"
    (root / "strategies" / "active").mkdir(parents=True)
    (root / "strategies" / "pending").mkdir(parents=True)
    (root / "strategies" / "retired").mkdir(parents=True)
    (root / "LESSONS.md").write_text("# Lessons\n")
    (root / "alpha_research.md").write_text("Emit from active strategies.")
    (root / "backtest_verification.md").write_text("Approve if sharpe >= 0.5 for this e2e.")
    (root / "strategy_genesis.md").write_text("Propose one strategy.")
    (root / "risk_rules.md").write_text("size_pct <= 5%.")
    repo = git.Repo.init(root)
    repo.git.add(A=True)
    repo.index.commit("init")
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
    return SkillsRepo(root)


@pytest.mark.asyncio
async def test_full_agent_loop_with_stubs(tmp_path, session_factory, starting_capital, monkeypatch):
    sr = _seed(tmp_path, session_factory)
    lessons = LessonsLog(sr)
    reg = StrategyRegistry(session_factory, sr)
    bus = Bus(fakeredis.aioredis.FakeRedis())

    # Stub AgentClient.run for all three agents
    def fake_run_for_genesis(self, messages, max_turns=10):
        # Simulate the genesis agent calling propose_strategy
        for spec in self.tools.values():
            if spec.name == "propose_strategy":
                spec.function(name="baked_sma", source_code=BAKED_STRATEGY,
                              hyperparams={"window": 5})
                break
        return "proposed baked_sma"

    def fake_run_for_checker(self, messages, max_turns=10):
        # Approve verdict
        return '{"verdict": "approve", "reason": "looks good for e2e"}'

    def fake_run_for_maker(self, messages, max_turns=10):
        return "ok"

    genesis_client = AgentClient(model="claude-opus-4-7", system_prompt="", tools=[])
    genesis = GenesisAgent(genesis_client, reg, sr, lessons, session_factory)
    monkeypatch.setattr(genesis_client, "run", fake_run_for_genesis.__get__(genesis_client))
    name = genesis.propose_once()
    assert name == "baked_sma"

    checker_client = AgentClient(model="claude-opus-4-7", system_prompt="", tools=[])
    checker = CheckerAgent(checker_client, reg, sr, lessons, session_factory, bus)
    monkeypatch.setattr(checker_client, "run", fake_run_for_checker.__get__(checker_client))
    verdict = checker.validate_strategy("baked_sma")
    assert verdict == "approved"

    with session_factory() as s:
        row = s.query(Strategy).filter_by(name="baked_sma").one()
        assert row.status == "active"

    # Maker tick
    maker_client = AgentClient(model="claude-sonnet-4-6", system_prompt="", tools=[])
    maker = MakerAgent(maker_client, reg, sr, lessons, session_factory, bus,
                       watermark_path=tmp_path / "wm.txt")
    monkeypatch.setattr(maker_client, "run", fake_run_for_maker.__get__(maker_client))
    maker.record_bar_seen(datetime(2026, 6, 1, 11, 5, tzinfo=UTC))
    assert maker.should_tick()
    emitted = await maker.tick()
    assert emitted >= 1

    # ---- Now run the executor and verify the signal becomes a fill ----
    sim = Simulator(starting_cash=starting_capital)
    ex = Executor(bus, session_factory, sim,
                  latest_prices={"BTCUSDT": Decimal("65000")})
    svc = ExecutorService(ex, bus, sim, session_factory)

    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.05)

    # Find the signal_id the maker just emitted and mark it approved
    with session_factory() as s:
        candidate_sig = s.query(Signal).filter_by(strategy_id="baked_sma",
                                                   status="candidate").first()
        assert candidate_sig is not None, "maker did not emit a candidate signal"
        candidate_sig.status = "approved"
        s.commit()
        signal_id = candidate_sig.id

    await bus.publish(CH_SIGNAL_VERIFIED, SignalVerified(
        signal_id=signal_id, verdict="approve"))
    await asyncio.sleep(0.3)
    await svc.stop()
    await asyncio.wait_for(task, timeout=1.0)

    with session_factory() as s:
        fills = s.query(Fill).all()
        assert len(fills) >= 1, "executor never produced a fill from the verified signal"
        pos = s.get(Position, "BTCUSDT")
        assert pos is not None
        assert pos.qty != Decimal("0"), "position not opened"


@pytest.mark.asyncio
async def test_reject_path_grows_lessons_and_retires_strategy(tmp_path, session_factory, monkeypatch):
    root = tmp_path / "skills"
    (root / "strategies" / "active").mkdir(parents=True)
    (root / "strategies" / "pending").mkdir(parents=True)
    (root / "strategies" / "retired").mkdir(parents=True)
    (root / "LESSONS.md").write_text("# Lessons\n")
    (root / "alpha_research.md").write_text("x")
    (root / "backtest_verification.md").write_text("Reject loose strategies.")
    (root / "strategy_genesis.md").write_text("Propose one.")
    (root / "risk_rules.md").write_text("x")
    repo = git.Repo.init(root)
    repo.git.add(A=True)
    repo.index.commit("init")
    with session_factory() as s:
        for i in range(300):
            s.add(Bar(symbol="BTCUSDT", timeframe="5m",
                      ts=datetime(2026, 6, 1, tzinfo=UTC) + timedelta(minutes=5 * i),
                      open=Decimal(str(60000 + i * 5)), high=Decimal(str(60100 + i * 5)),
                      low=Decimal(str(59900 + i * 5)), close=Decimal(str(60050 + i * 5)),
                      volume=Decimal("1")))
        s.commit()

    sr = SkillsRepo(root)
    lessons = LessonsLog(sr)
    reg = StrategyRegistry(session_factory, sr)
    bus = Bus(fakeredis.aioredis.FakeRedis())

    BAKED = '''
NAME = "bad_strat"
DEFAULT_HYPERPARAMS = {}
def generate_signals(bars, hyperparams):
    return []
'''
    reg.register_pending("bad_strat", BAKED, {}, actor="genesis")

    checker_client = AgentClient(model="claude-opus-4-7", system_prompt="", tools=[])
    checker = CheckerAgent(checker_client, reg, sr, lessons, session_factory, bus)

    def fake_reject(messages, max_turns=10):
        return '{"verdict": "reject", "reason": "sharpe too low"}'

    monkeypatch.setattr(checker_client, "run", fake_reject)
    verdict = checker.validate_strategy("bad_strat")
    assert verdict == "rejected"

    with session_factory() as s:
        row = s.query(Strategy).filter_by(name="bad_strat").one()
        assert row.status == "retired"

    body = sr.read("LESSONS.md")
    assert "rejected bad_strat" in body
    assert "sharpe too low" in body
