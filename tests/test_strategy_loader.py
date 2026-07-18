from pathlib import Path

import git
import pytest

from loophedge.memory.skills import SkillsRepo
from loophedge.strategies.loader import load_strategy


SAMPLE_STRATEGY = '''
NAME = "always_long_btc"
DEFAULT_HYPERPARAMS = {"size_pct": "0.02"}

def generate_signals(bars, hyperparams):
    from decimal import Decimal
    if not bars:
        return []
    return [{"symbol": "BTCUSDT", "side": "long",
             "size_pct": Decimal(str(hyperparams["size_pct"])),
             "ts": bars[-1].ts}]
'''


def _init_skills_with_strategy(tmp_path, location="active"):
    root = tmp_path / "skills"
    (root / "strategies" / location).mkdir(parents=True)
    (root / "LESSONS.md").write_text("# l\n")
    (root / "strategies" / location / "always_long_btc.py").write_text(SAMPLE_STRATEGY)
    repo = git.Repo.init(root)
    repo.git.add(A=True)
    repo.index.commit("init")
    return SkillsRepo(root)


def test_load_strategy_from_active(tmp_path):
    sr = _init_skills_with_strategy(tmp_path, "active")
    mod = load_strategy("always_long_btc", sr)
    assert mod.NAME == "always_long_btc"
    assert mod.DEFAULT_HYPERPARAMS["size_pct"] == "0.02"


def test_load_strategy_from_pending_if_not_in_active(tmp_path):
    sr = _init_skills_with_strategy(tmp_path, "pending")
    mod = load_strategy("always_long_btc", sr)
    assert mod.NAME == "always_long_btc"


def test_load_strategy_not_found_raises(tmp_path):
    sr = _init_skills_with_strategy(tmp_path, "active")
    with pytest.raises(FileNotFoundError):
        load_strategy("does_not_exist", sr)


def test_loaded_strategy_runs(tmp_path):
    from datetime import UTC, datetime
    from decimal import Decimal
    from loophedge.models import Bar

    sr = _init_skills_with_strategy(tmp_path, "active")
    mod = load_strategy("always_long_btc", sr)
    bar = Bar(symbol="BTCUSDT", timeframe="5m",
              ts=datetime(2026, 6, 29, tzinfo=UTC),
              open=Decimal("1"), high=Decimal("1"), low=Decimal("1"),
              close=Decimal("60000"), volume=Decimal("1"))
    sigs = mod.generate_signals([bar], mod.DEFAULT_HYPERPARAMS)
    assert sigs[0]["symbol"] == "BTCUSDT"
    assert sigs[0]["size_pct"] == Decimal("0.02")
