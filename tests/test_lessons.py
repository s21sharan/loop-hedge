from datetime import UTC, datetime
from pathlib import Path

import git

from loophedge.memory.lessons import LessonsLog
from loophedge.memory.skills import SkillsRepo


def _init(tmp_path: Path) -> SkillsRepo:
    root = tmp_path / "skills"
    root.mkdir()
    (root / "LESSONS.md").write_text("# Lessons Learned\n\n")
    repo = git.Repo.init(root)
    repo.index.add(["LESSONS.md"])
    repo.index.commit("init")
    return SkillsRepo(root)


def test_append_writes_bullet_and_commits(tmp_path):
    sr = _init(tmp_path)
    log = LessonsLog(sr)
    log.append("checker", datetime(2026, 6, 29, 14, 22, tzinfo=UTC),
                "rejected XRP long: backtest sharpe 0.7 below threshold 1.0")
    body = sr.read("LESSONS.md")
    assert "rejected XRP long" in body
    assert "2026-06-29T14:22" in body
    assert "[checker]" in body


def test_recent_returns_last_n(tmp_path):
    sr = _init(tmp_path)
    log = LessonsLog(sr)
    for i in range(5):
        log.append("checker", datetime(2026, 6, 29, 10, i, tzinfo=UTC), f"lesson {i}")
    last3 = log.recent(3)
    assert len(last3) == 3
    assert "lesson 4" in last3[-1]
    assert "lesson 2" in last3[0]
