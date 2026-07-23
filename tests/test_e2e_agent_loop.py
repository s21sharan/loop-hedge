from datetime import UTC, datetime, timedelta
from decimal import Decimal

import fakeredis.aioredis
import git
import pytest

from loophedge.agents.checker import CheckerAgent
from loophedge.agents.client import AgentClient
from loophedge.agents.genesis import GenesisAgent
from loophedge.agents.maker import MakerAgent
from loophedge.bus import Bus
from loophedge.memory.lessons import LessonsLog
from loophedge.memory.skills import SkillsRepo
from loophedge.models import Bar, Strategy
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
    verdict = checker.validate("baked_sma")
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
