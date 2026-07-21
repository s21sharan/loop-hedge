from pathlib import Path

import git
import pytest

from loophedge.memory.skills import SkillsRepo
from loophedge.models import Strategy
from loophedge.strategies.registry import StrategyRegistry


SAMPLE = '''
NAME = "ma_cross_btc"
DEFAULT_HYPERPARAMS = {"fast": 5, "slow": 20}
def generate_signals(bars, hyperparams): return []
'''


def _skills(tmp_path):
    root = tmp_path / "skills"
    (root / "strategies" / "active").mkdir(parents=True)
    (root / "strategies" / "pending").mkdir(parents=True)
    (root / "strategies" / "retired").mkdir(parents=True)
    (root / "LESSONS.md").write_text("# l\n")
    repo = git.Repo.init(root)
    repo.git.add(A=True)
    repo.index.commit("init")
    return SkillsRepo(root)


def test_register_pending_writes_file_and_db_row(tmp_path, session_factory):
    sr = _skills(tmp_path)
    reg = StrategyRegistry(session_factory, sr)
    sid = reg.register_pending("ma_cross_btc", SAMPLE, {"fast": 5, "slow": 20}, actor="genesis")
    assert (sr.root / "strategies" / "pending" / "ma_cross_btc.py").exists()
    with session_factory() as s:
        row = s.get(Strategy, sid)
        assert row.status == "pending"
        assert row.hyperparams == {"fast": 5, "slow": 20}


def test_promote_moves_file_and_updates_status(tmp_path, session_factory):
    sr = _skills(tmp_path)
    reg = StrategyRegistry(session_factory, sr)
    sid = reg.register_pending("ma_cross_btc", SAMPLE, {}, actor="genesis")
    reg.promote("ma_cross_btc", actor="checker", reason="backtest sharpe 1.4")
    assert (sr.root / "strategies" / "active" / "ma_cross_btc.py").exists()
    assert not (sr.root / "strategies" / "pending" / "ma_cross_btc.py").exists()
    with session_factory() as s:
        row = s.get(Strategy, sid)
        assert row.status == "active"
        assert row.promoted_reason == "backtest sharpe 1.4"


def test_list_active_only_returns_active(tmp_path, session_factory):
    sr = _skills(tmp_path)
    reg = StrategyRegistry(session_factory, sr)
    sid_a = reg.register_pending("a", SAMPLE.replace('"ma_cross_btc"', '"a"'), {}, "genesis")
    reg.register_pending("b", SAMPLE.replace('"ma_cross_btc"', '"b"'), {}, "genesis")
    reg.promote("a", "checker", "ok")
    actives = reg.list_active()
    assert len(actives) == 1
    assert actives[0].id == sid_a


def test_retire_moves_to_retired(tmp_path, session_factory):
    sr = _skills(tmp_path)
    reg = StrategyRegistry(session_factory, sr)
    reg.register_pending("ma_cross_btc", SAMPLE, {}, "genesis")
    reg.promote("ma_cross_btc", "checker", "ok")
    reg.retire("ma_cross_btc", "genesis", "underperformed")
    assert (sr.root / "strategies" / "retired" / "ma_cross_btc.py").exists()
    assert not (sr.root / "strategies" / "active" / "ma_cross_btc.py").exists()


def test_retire_from_pending(tmp_path, session_factory):
    """Retire a strategy that was rejected before promotion (still in pending/)."""
    sr = _skills(tmp_path)
    reg = StrategyRegistry(session_factory, sr)
    reg.register_pending("rejected_strat", SAMPLE.replace('"ma_cross_btc"', '"rejected_strat"'),
                        {}, "genesis")
    reg.retire("rejected_strat", actor="checker", reason="rejected by checker")
    assert (sr.root / "strategies" / "retired" / "rejected_strat.py").exists()
    assert not (sr.root / "strategies" / "pending" / "rejected_strat.py").exists()
    with session_factory() as s:
        row = s.query(Strategy).filter_by(name="rejected_strat").one()
        assert row.status == "retired"
        assert row.retired_reason == "rejected by checker"
