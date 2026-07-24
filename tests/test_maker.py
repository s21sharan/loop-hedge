from datetime import UTC, datetime

import fakeredis.aioredis
import git
import pytest

from loophedge.agents.client import AgentClient
from loophedge.agents.maker import MakerAgent
from loophedge.bus import Bus
from loophedge.memory.lessons import LessonsLog
from loophedge.memory.skills import SkillsRepo
from loophedge.strategies.registry import StrategyRegistry


def _seed(tmp_path):
    root = tmp_path / "skills"
    (root / "strategies" / "active").mkdir(parents=True)
    (root / "strategies" / "pending").mkdir(parents=True)
    (root / "strategies" / "retired").mkdir(parents=True)
    (root / "LESSONS.md").write_text("# l\n")
    (root / "alpha_research.md").write_text("Emit signals from active strategies.")
    repo = git.Repo.init(root)
    repo.git.add(A=True)
    repo.index.commit("init")
    return SkillsRepo(root)


def test_should_tick_false_when_no_bar_seen(tmp_path, session_factory):
    sr = _seed(tmp_path)
    lessons = LessonsLog(sr)
    reg = StrategyRegistry(session_factory, sr)
    bus = Bus(fakeredis.aioredis.FakeRedis())
    client = AgentClient(model="claude-sonnet-4-6", system_prompt="x", tools=[])
    wm = tmp_path / "watermark.txt"
    maker = MakerAgent(client, reg, sr, lessons, session_factory, bus, wm)
    assert maker.should_tick() is False


def test_should_tick_true_after_new_bar(tmp_path, session_factory):
    sr = _seed(tmp_path)
    lessons = LessonsLog(sr)
    reg = StrategyRegistry(session_factory, sr)
    bus = Bus(fakeredis.aioredis.FakeRedis())
    client = AgentClient(model="claude-sonnet-4-6", system_prompt="x", tools=[])
    wm = tmp_path / "watermark.txt"
    maker = MakerAgent(client, reg, sr, lessons, session_factory, bus, wm)
    maker.record_bar_seen(datetime(2026, 6, 29, 12, 0, tzinfo=UTC))
    assert maker.should_tick() is True


def test_should_tick_false_after_tick_until_new_bar(tmp_path, session_factory):
    sr = _seed(tmp_path)
    lessons = LessonsLog(sr)
    reg = StrategyRegistry(session_factory, sr)
    bus = Bus(fakeredis.aioredis.FakeRedis())
    client = AgentClient(model="claude-sonnet-4-6", system_prompt="x", tools=[])
    wm = tmp_path / "watermark.txt"
    maker = MakerAgent(client, reg, sr, lessons, session_factory, bus, wm)
    maker.record_bar_seen(datetime(2026, 6, 29, 12, 0, tzinfo=UTC))
    maker._mark_ticked(datetime(2026, 6, 29, 12, 0, tzinfo=UTC))
    assert maker.should_tick() is False
    maker.record_bar_seen(datetime(2026, 6, 29, 12, 5, tzinfo=UTC))
    assert maker.should_tick() is True


@pytest.mark.asyncio
async def test_tick_marks_watermark_with_seen_ts(tmp_path, session_factory):
    sr = _seed(tmp_path)
    lessons = LessonsLog(sr)
    reg = StrategyRegistry(session_factory, sr)
    bus = Bus(fakeredis.aioredis.FakeRedis())
    client = AgentClient(model="claude-sonnet-4-6", system_prompt="x", tools=[])
    wm = tmp_path / "watermark.txt"
    maker = MakerAgent(client, reg, sr, lessons, session_factory, bus, wm)

    seen_ts = datetime(2026, 6, 29, 12, 5, tzinfo=UTC)
    maker.record_bar_seen(seen_ts)
    # No active strategies, so tick emits 0 but still updates ticked watermark.
    emitted = await maker.tick()
    assert emitted == 0
    assert maker.should_tick() is False  # ticked now matches seen
