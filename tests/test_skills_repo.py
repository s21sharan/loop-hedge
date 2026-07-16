from pathlib import Path

import pytest

from loophedge.memory.skills import SkillsRepo


def _init_skills(tmp_path: Path) -> Path:
    import git
    root = tmp_path / "skills"
    root.mkdir()
    (root / "alpha_research.md").write_text("# initial\n")
    repo = git.Repo.init(root)
    repo.index.add(["alpha_research.md"])
    repo.index.commit("init")
    return root


def test_read_returns_file_content(tmp_path):
    root = _init_skills(tmp_path)
    sr = SkillsRepo(root)
    assert sr.read("alpha_research.md").startswith("# initial")


def test_write_creates_commit_with_actor_and_reason(tmp_path):
    import git
    root = _init_skills(tmp_path)
    sr = SkillsRepo(root)
    sha = sr.write("LESSONS.md", "first lesson\n", actor="checker", reason="rejected XRP long")
    assert len(sha) == 40
    repo = git.Repo(root)
    last = repo.head.commit
    assert last.hexsha == sha
    assert "checker" in last.message
    assert "rejected XRP long" in last.message


def test_write_outside_root_rejected(tmp_path):
    root = _init_skills(tmp_path)
    sr = SkillsRepo(root)
    with pytest.raises(ValueError):
        sr.write("../escape.md", "bad", "actor", "reason")


def test_read_missing_file_raises(tmp_path):
    root = _init_skills(tmp_path)
    sr = SkillsRepo(root)
    with pytest.raises(FileNotFoundError):
        sr.read("does_not_exist.md")
