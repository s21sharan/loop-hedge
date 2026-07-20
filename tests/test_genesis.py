import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import git
import pytest

from loophedge.agents.client import AgentClient
from loophedge.agents.genesis import GenesisAgent
from loophedge.memory.lessons import LessonsLog
from loophedge.memory.skills import SkillsRepo
from loophedge.models import Bar, Strategy
from loophedge.strategies.registry import StrategyRegistry


def _seed_skills(tmp_path):
    root = tmp_path / "skills"
    (root / "strategies" / "active").mkdir(parents=True)
    (root / "strategies" / "pending").mkdir(parents=True)
    (root / "strategies" / "retired").mkdir(parents=True)
    (root / "LESSONS.md").write_text("# Lessons Learned\n")
    (root / "strategy_genesis.md").write_text(
        "Propose a strategy with NAME, DEFAULT_HYPERPARAMS, generate_signals."
    )
    (root / "risk_rules.md").write_text("Per-trade size <= 5%. No leverage.")
    repo = git.Repo.init(root)
    repo.git.add(A=True)
    repo.index.commit("init")
    return SkillsRepo(root)


def _seed_bars(session_factory, n=200):
    with session_factory() as s:
        for i in range(n):
            s.add(Bar(symbol="BTCUSDT", timeframe="5m",
                      ts=datetime(2026, 6, 1, tzinfo=UTC) + timedelta(minutes=5 * i),
                      open=Decimal(str(60000 + i)), high=Decimal(str(60100 + i)),
                      low=Decimal(str(59900 + i)), close=Decimal(str(60050 + i)),
                      volume=Decimal("1")))
        s.commit()


def _cassette_recorded() -> bool:
    p = Path(__file__).parent / "cassettes" / "genesis_proposes_strategy.yaml"
    return p.exists() and p.stat().st_size > 200  # >200 bytes = real cassette


@pytest.mark.skipif(not _cassette_recorded(),
                     reason="cassette is placeholder; record with ANTHROPIC_LIVE_RECORD=1")
def test_genesis_proposes_strategy(tmp_path, session_factory, vcr_cassette):
    sr = _seed_skills(tmp_path)
    _seed_bars(session_factory)
    lessons = LessonsLog(sr)
    registry = StrategyRegistry(session_factory, sr)

    with vcr_cassette("genesis_proposes_strategy"):
        client = AgentClient(
            model="claude-opus-4-7",
            system_prompt="You are a quant researcher.",
            tools=[],  # populated inside GenesisAgent
        )
        agent = GenesisAgent(client, registry, sr, lessons, session_factory)
        name = agent.propose_once()

    assert name is not None
    with session_factory() as s:
        row = s.query(Strategy).filter_by(name=name).one()
        assert row.status == "pending"
    assert (sr.root / "strategies" / "pending" / f"{name}.py").exists()
