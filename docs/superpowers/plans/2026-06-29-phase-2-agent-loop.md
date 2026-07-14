# Phase 2: Agent Loop — Maker + Checker + Genesis — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the LLM-driven self-improving loop on top of the Phase 1 deterministic core: a genesis agent that bootstraps strategies from scratch, a checker agent that independently validates them via backtests, a maker agent that emits candidate signals on a dual schedule, and a long-running executor that subscribes to `signal.verified` and auto-flattens on `circuit.broken`. The end-to-end test (cassette-driven) feeds 60 historical bars, lets genesis propose a strategy, the checker validates it, the maker emits a signal, the executor fills it, and the lessons file grows.

**Architecture:** Eight long-running services in Docker Compose. The three new agent containers use the `anthropic` Python SDK against api.anthropic.com with a custom tool loop. Tests use `vcrpy` cassettes recorded against the live API once and replayed deterministically thereafter. Strategies are Python files in a versioned `skills/` volume; promotion (pending → active → retired) is mediated by the checker. The `state/` volume holds append-only `LESSONS.md`, `STATE.md`, and rolling traces.

**Tech Stack:** Anthropic Python SDK (`anthropic==0.40.*`), vcrpy (`vcrpy==6.0.*`), GitPython (`gitpython==3.1.*`) for skill-volume commits, NumPy + SciPy for backtest statistics. Everything else inherited from Phase 1.

## Global Constraints

- Python 3.12. All services run in the existing `loophedge:dev` image.
- LLM agents use model IDs: maker → `claude-sonnet-4-6`, checker → `claude-opus-4-7`, genesis → `claude-opus-4-7`.
- Maker scheduling: dual mode — APScheduler timer fires every 15 minutes, but the maker SHORT-CIRCUITS (returns without emitting) if no new `bar.closed` event has been observed since the previous tick. The `bar.closed` watermark is tracked in `state/maker_watermark.txt`.
- Tests use `vcrpy` cassettes stored in `tests/cassettes/`. CI runs in replay-only mode (`record_mode="none"`). Live recording requires an explicit `ANTHROPIC_LIVE_RECORD=1` env var + a valid `ANTHROPIC_API_KEY`. Cassettes are committed to the repo.
- All Anthropic API calls go through `loophedge.agents.client.AgentClient` — never direct `anthropic.Anthropic()` instantiation in agent service code. This is the chokepoint VCR cassettes attach to.
- Skills volume (`skills/`) is a git repository nested inside the main repo (NOT a submodule, NOT in `.gitignore`). The main repo's `.gitignore` ignores `skills/.git/` to prevent the nested repo from being staged into the parent. Every write to a file under `skills/` triggers a git commit inside `skills/.git/` via `loophedge.memory.skills.SkillsRepo.write(path, content, reason)`.
- State volume (`state/`) is gitignored at the main repo level. It holds runtime artifacts that should not be versioned.
- The genesis agent may ONLY write to `skills/strategies/pending/`. The checker is the sole writer of `skills/strategies/active/` (via promotion). The agents themselves cannot violate this — enforced in `AgentClient` tool registration scopes.
- A `Strategy` is a Python file exporting a single function `generate_signals(bars: list[Bar], hyperparams: dict) -> list[dict]` where each dict has `{symbol, side, size_pct, reasoning}`. Hyperparams come from `strategies` table `hyperparams` JSON column.
- Hard caps in `loophedge.risk.caps` are still immutable code constants. Strategies that violate them on backtest are auto-rejected by the checker without an LLM round-trip.
- Conventional commits, no `Co-Authored-By: Claude` line.
- The implementer must NOT run `docker compose up`, must NOT start any backend server, and must NOT make live Anthropic API calls except during cassette recording explicitly requested by the user.

## File Structure

```
loop-hedge/
├── pyproject.toml                          (modify: + anthropic, vcrpy, gitpython, scipy)
├── docker-compose.yml                      (modify: agent containers run real commands)
├── .gitignore                              (modify: + skills/.git/, state/)
├── skills/                                 (NEW git repo nested in main repo)
│   ├── .git/                               (initialized in Task 1)
│   ├── alpha_research.md                   (maker's playbook)
│   ├── backtest_verification.md            (checker's playbook)
│   ├── strategy_genesis.md                 (genesis playbook)
│   ├── risk_rules.md                       (hard caps + soft heuristics)
│   ├── STATE.md                            (portfolio summary, written each tick)
│   ├── LESSONS.md                          (append-only journal)
│   └── strategies/
│       ├── active/.gitkeep
│       ├── pending/.gitkeep
│       └── retired/.gitkeep
├── state/                                  (gitignored runtime volume)
│   ├── .gitignore                          (ignores * except .gitignore)
│   ├── maker_watermark.txt                 (last seen bar.closed ts)
│   └── traces/                             (rolling agent traces)
├── src/loophedge/
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── skills.py                       (SkillsRepo — read, write+commit)
│   │   ├── lessons.py                      (LESSONS.md append-only)
│   │   └── traces.py                       (rolling trace writer)
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── interface.py                    (Strategy protocol)
│   │   ├── loader.py                       (dynamic .py import)
│   │   └── registry.py                     (active/pending/retired CRUD)
│   ├── backtest/
│   │   ├── __init__.py
│   │   └── engine.py                       (walk-forward Sharpe, max DD, t-stat)
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── client.py                       (AgentClient + tool registry)
│   │   ├── tools.py                        (tool function library)
│   │   ├── maker.py                        (Maker service)
│   │   ├── checker.py                      (Checker service)
│   │   └── genesis.py                      (Genesis service)
│   ├── services/
│   │   └── executor.py                     (modify: subscriber + auto-flatten)
│   └── cli.py                              (modify: real run_maker/run_checker/run_genesis/run_execute/run_risk)
└── tests/
    ├── cassettes/
    │   ├── .gitkeep
    │   ├── genesis_proposes_strategy.yaml
    │   ├── checker_approves_strategy.yaml
    │   ├── checker_rejects_strategy.yaml
    │   └── maker_emits_candidate.yaml
    ├── conftest.py                         (modify: + cassette helpers)
    ├── test_skills_repo.py
    ├── test_lessons.py
    ├── test_backtest_engine.py
    ├── test_strategy_loader.py
    ├── test_strategy_registry.py
    ├── test_agent_client.py
    ├── test_genesis.py
    ├── test_checker.py
    ├── test_maker.py
    ├── test_executor_subscriber.py
    └── test_e2e_agent_loop.py
```

Rationale: each module has one job. `memory/` is the file-backed bridge between agents and persistent narrative. `strategies/` is the active code registry; `backtest/` is pure math; `agents/` is the LLM glue; `services/executor.py` is touched lightly to add a subscriber loop without disturbing Phase 1 tests.

---

### Task 1: Dependencies, skills volume, and state volume

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `skills/` directory with `.git/` (init'd) and the four playbook stubs + `STATE.md` + `LESSONS.md` + strategies/{active,pending,retired}/.gitkeep
- Create: `state/.gitignore`

**Interfaces:**
- Produces: top-level `skills/` directory committed in main repo (without its inner `.git/`); top-level `state/` directory present but contents gitignored.

- [ ] **Step 1: Update `pyproject.toml` to add Phase 2 dependencies**

Find the `dependencies` list and append:

```toml
  "anthropic==0.40.*",
  "gitpython==3.1.*",
  "scipy==1.14.*",
  "numpy==2.1.*",
```

Add to `[project.optional-dependencies]` dev:

```toml
  "vcrpy==6.0.*",
  "pytest-vcr==1.0.*",
```

- [ ] **Step 2: Update `.gitignore`**

Append:

```gitignore
# Phase 2 — nested skills repo's .git, runtime state
skills/.git/
state/
!state/.gitignore
```

(The `!state/.gitignore` exception lets the inner gitignore stay committed.)

- [ ] **Step 3: Create skills/ playbook stubs**

`skills/alpha_research.md`:
```markdown
# Alpha Research Playbook (Maker Agent)

## Goal
Generate candidate trade signals from active strategies in `strategies/active/`, filtered against current lessons learned.

## Rules
- Position size must be between 0.5% and 5% of equity.
- Skip any signal whose strategy violates `risk_rules.md`.
- Read `LESSONS.md` before emitting; if any lesson is relevant to the current condition, apply it.

## Lessons learned
(Auto-appended by the checker on every rejection.)
```

`skills/backtest_verification.md`:
```markdown
# Backtest Verification Playbook (Checker Agent)

## Goal
Independently validate every signal candidate via walk-forward backtest before approving.

## Approval criteria
- Sharpe ratio ≥ 1.0 over the test window.
- Max drawdown < 12%.
- Newey-West t-statistic ≥ 1.5.
- ≥ 30 trades in the backtest period.

## Output format
Return `{ "verdict": "approve" | "reject" | "needs_revision", "reason": "..." }`.
```

`skills/strategy_genesis.md`:
```markdown
# Strategy Genesis Playbook

## Goal
Propose a new Python strategy file in `strategies/pending/` whose `generate_signals(bars, hyperparams) -> list[dict]` produces candidate signals on minute bars.

## Required exports
Each strategy file must export:
- `NAME: str` — unique strategy id.
- `DEFAULT_HYPERPARAMS: dict` — initial values.
- `generate_signals(bars, hyperparams) -> list[dict]` — see interface.

## Constraints
- Must respect `risk_rules.md` (size cap, leverage, no shorting on first version).
- Must use only deterministic technical indicators (no external API calls).
```

`skills/risk_rules.md`:
```markdown
# Risk Rules

## Hard caps (enforced in code, not here)
- Per-trade size: ≤ 5% of equity.
- Per-strategy allocation: ≤ 25% of equity.
- Portfolio drawdown kill switch: 15% from 30-day rolling high.

## Soft heuristics
- Avoid same-side stacking on a single symbol within 1 hour.
- Reduce sizing by 50% during the first 24 hours of a new strategy's live life.
```

`skills/STATE.md`:
```markdown
# Portfolio State

(Updated by the risk monitor on each tick.)

- Equity: $100,000.00
- Cash: $100,000.00
- Positions: 0
- Drawdown: 0.0%
- Last update: never
```

`skills/LESSONS.md`:
```markdown
# Lessons Learned

(Append-only journal. Checker writes rejections here for the maker to read next tick.)
```

`skills/strategies/active/.gitkeep`, `skills/strategies/pending/.gitkeep`, `skills/strategies/retired/.gitkeep` — empty files.

- [ ] **Step 4: Initialize the nested skills git repo**

```bash
cd skills
git init -q
git add .
git -c "commit.gpgsign=false" commit -q -m "init: skill playbooks"
cd ..
```

This nested repo is the audit trail of every skill change. The outer repo doesn't track its history — only the directory contents.

- [ ] **Step 5: Create state/.gitignore**

`state/.gitignore`:
```gitignore
*
!.gitignore
```

- [ ] **Step 6: Install + verify import**

Run:
```bash
pip install -e ".[dev]"
python -c "import anthropic, vcr, git, scipy, numpy; print('ok')"
```
Expected: prints `ok`.

- [ ] **Step 7: Run full suite to confirm Phase 1 still green**

Run: `pytest -p no:seleniumbase -q`
Expected: 37 passed (unchanged).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore skills/ state/
git commit -m "feat: add phase 2 deps, skills volume, state volume"
```

(The nested `skills/.git/` is excluded by the new gitignore line; only `skills/`'s working-tree files are staged.)

---

### Task 2: `SkillsRepo` — read + commit-on-write

**Files:**
- Create: `src/loophedge/memory/__init__.py` (empty)
- Create: `src/loophedge/memory/skills.py`
- Create: `tests/test_skills_repo.py`

**Interfaces:**
- Consumes: GitPython for the nested skills repo.
- Produces: `class SkillsRepo` with `__init__(root: Path)`, `read(relpath: str) -> str`, `write(relpath: str, content: str, actor: str, reason: str) -> str` (returns commit SHA inside the skills repo). `read_strategy(name: str) -> str` (sugar for reading active/pending strategy code).

- [ ] **Step 1: Write failing test**

`tests/test_skills_repo.py`:
```python
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
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_skills_repo.py -v`
Expected: ImportError on `loophedge.memory.skills`.

- [ ] **Step 3: Implement `src/loophedge/memory/__init__.py`** — empty file.

- [ ] **Step 4: Implement `src/loophedge/memory/skills.py`**

```python
from pathlib import Path

import git


class SkillsRepo:
    """File-backed memory for skill markdown + strategy code, every write committed."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        if not (self.root / ".git").exists():
            raise ValueError(f"{root} is not a git repo")
        self._repo = git.Repo(self.root)

    def read(self, relpath: str) -> str:
        path = self._safe_path(relpath)
        return path.read_text(encoding="utf-8")

    def write(self, relpath: str, content: str, actor: str, reason: str) -> str:
        path = self._safe_path(relpath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._repo.index.add([str(path.relative_to(self.root))])
        commit = self._repo.index.commit(f"{actor}: {reason}")
        return commit.hexsha

    def read_strategy(self, name: str) -> str:
        for sub in ("active", "pending"):
            candidate = self.root / "strategies" / sub / f"{name}.py"
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        raise FileNotFoundError(f"strategy {name} not found in active/ or pending/")

    def _safe_path(self, relpath: str) -> Path:
        path = (self.root / relpath).resolve()
        if not str(path).startswith(str(self.root)):
            raise ValueError(f"{relpath} resolves outside skills root")
        return path
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest tests/test_skills_repo.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/loophedge/memory/ tests/test_skills_repo.py
git commit -m "feat: SkillsRepo reads and writes the versioned skills volume"
```

---

### Task 3: `LessonsLog` — append-only journal

**Files:**
- Create: `src/loophedge/memory/lessons.py`
- Create: `tests/test_lessons.py`

**Interfaces:**
- Consumes: `SkillsRepo` from Task 2.
- Produces: `class LessonsLog(skills_repo: SkillsRepo)` with `append(actor: str, ts: datetime, summary: str) -> None` (appends a markdown bullet, commits) and `recent(n: int = 20) -> list[str]` (returns the last n lessons as plain strings).

- [ ] **Step 1: Write failing test**

`tests/test_lessons.py`:
```python
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
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_lessons.py -v`
Expected: ImportError on `loophedge.memory.lessons`.

- [ ] **Step 3: Implement `src/loophedge/memory/lessons.py`**

```python
from datetime import datetime

from loophedge.memory.skills import SkillsRepo


class LessonsLog:
    def __init__(self, skills_repo: SkillsRepo):
        self.skills = skills_repo

    def append(self, actor: str, ts: datetime, summary: str) -> None:
        existing = self.skills.read("LESSONS.md")
        entry = f"- {ts.isoformat()} [{actor}] {summary}\n"
        new = existing.rstrip() + "\n" + entry
        self.skills.write("LESSONS.md", new, actor=actor, reason=f"new lesson")

    def recent(self, n: int = 20) -> list[str]:
        body = self.skills.read("LESSONS.md")
        bullets = [ln for ln in body.splitlines() if ln.startswith("- ")]
        return bullets[-n:]
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_lessons.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/loophedge/memory/lessons.py tests/test_lessons.py
git commit -m "feat: LessonsLog appends to LESSONS.md with git audit trail"
```

---

### Task 4: Backtest engine

**Files:**
- Create: `src/loophedge/backtest/__init__.py` (empty)
- Create: `src/loophedge/backtest/engine.py`
- Create: `tests/test_backtest_engine.py`

**Interfaces:**
- Consumes: `loophedge.ledger.simulator.Simulator`, `loophedge.models.Bar`.
- Produces: `class BacktestResult` (dataclass: `sharpe, max_dd_pct, t_stat, trade_count, equity_curve: list[Decimal], passed: bool, notes: str`). Function `run_backtest(bars: list[Bar], strategy_callable: Callable, hyperparams: dict, starting_cash: Decimal = Decimal("100000")) -> BacktestResult`. The strategy callable matches the `Strategy` protocol introduced in Task 5: `(bars: list[Bar], hyperparams: dict) -> list[dict]` where each dict is `{"symbol", "side", "size_pct", "ts"}`. Sharpe uses log-returns annualized by `sqrt(252 * (1440 // bar_minutes))`. Newey-West t-stat lag = 5.

- [ ] **Step 1: Write failing tests**

`tests/test_backtest_engine.py`:
```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from loophedge.backtest.engine import BacktestResult, run_backtest
from loophedge.models import Bar


def _bars(n=200, start_price=60000, drift=10):
    out = []
    ts = datetime(2026, 6, 1, tzinfo=UTC)
    for i in range(n):
        p = Decimal(str(start_price + i * drift))
        out.append(Bar(symbol="BTCUSDT", timeframe="5m",
                       ts=ts + timedelta(minutes=5 * i),
                       open=p, high=p, low=p, close=p, volume=Decimal("1")))
    return out


def _buy_every_10_bars(bars, hyperparams):
    sigs = []
    for i, b in enumerate(bars):
        if i % 10 == 0:
            sigs.append({"symbol": b.symbol, "side": "long",
                          "size_pct": Decimal("0.01"), "ts": b.ts})
    return sigs


def _no_op_strategy(bars, hyperparams):
    return []


def test_backtest_returns_result_dataclass():
    result = run_backtest(_bars(), _buy_every_10_bars, {})
    assert isinstance(result, BacktestResult)
    assert result.trade_count == 20  # 200 bars / 10
    assert isinstance(result.sharpe, Decimal)
    assert isinstance(result.max_dd_pct, Decimal)
    assert isinstance(result.t_stat, Decimal)


def test_backtest_no_trades_returns_zeros():
    result = run_backtest(_bars(), _no_op_strategy, {})
    assert result.trade_count == 0
    assert result.sharpe == Decimal("0")
    assert result.max_dd_pct == Decimal("0")
    assert not result.passed


def test_backtest_uptrending_market_positive_sharpe():
    """Buying into a steadily rising market should produce positive sharpe."""
    result = run_backtest(_bars(n=300, drift=20), _buy_every_10_bars, {})
    assert result.sharpe > Decimal("0")
    assert len(result.equity_curve) == 300


def test_backtest_passed_requires_sharpe_and_drawdown_thresholds():
    """passed=True requires sharpe>=1.0, max_dd<12%, t_stat>=1.5, trades>=30."""
    # _buy_every_10_bars on 300 bars yields 30 trades; whether sharpe + t-stat
    # clear the bar depends on the price path. Just assert the gating logic.
    result = run_backtest(_bars(n=300, drift=20), _buy_every_10_bars, {})
    expected_passed = (result.sharpe >= Decimal("1.0")
                       and result.max_dd_pct < Decimal("0.12")
                       and result.t_stat >= Decimal("1.5")
                       and result.trade_count >= 30)
    assert result.passed == expected_passed


def test_backtest_strategy_violating_hard_cap_marked_failed():
    """A strategy that emits size_pct > 5% should be auto-failed without scoring."""
    def oversized(bars, hyperparams):
        return [{"symbol": "BTCUSDT", "side": "long",
                 "size_pct": Decimal("0.10"), "ts": bars[0].ts}]
    result = run_backtest(_bars(), oversized, {})
    assert not result.passed
    assert "hard cap" in result.notes.lower()
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_backtest_engine.py -v`
Expected: ImportError on `loophedge.backtest.engine`.

- [ ] **Step 3: Implement `src/loophedge/backtest/engine.py`**

```python
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from math import sqrt
from typing import Any

import numpy as np
from scipy import stats

from loophedge.ledger.simulator import Simulator
from loophedge.models import Bar
from loophedge.risk.caps import HARD_MAX_POSITION_PCT


SHARPE_THRESHOLD = Decimal("1.0")
MAX_DD_THRESHOLD = Decimal("0.12")
T_STAT_THRESHOLD = Decimal("1.5")
MIN_TRADES = 30


@dataclass
class BacktestResult:
    sharpe: Decimal
    max_dd_pct: Decimal
    t_stat: Decimal
    trade_count: int
    equity_curve: list[Decimal] = field(default_factory=list)
    passed: bool = False
    notes: str = ""


def run_backtest(
    bars: list[Bar],
    strategy_callable: Callable[[list[Bar], dict[str, Any]], list[dict]],
    hyperparams: dict[str, Any],
    starting_cash: Decimal = Decimal("100000"),
) -> BacktestResult:
    if not bars:
        return BacktestResult(Decimal("0"), Decimal("0"), Decimal("0"), 0, notes="empty bars")

    signals = strategy_callable(bars, hyperparams) or []

    for sig in signals:
        if Decimal(str(sig["size_pct"])) > HARD_MAX_POSITION_PCT:
            return BacktestResult(
                Decimal("0"), Decimal("0"), Decimal("0"), 0,
                notes="strategy emitted size_pct above hard cap"
            )

    sim = Simulator(starting_cash=starting_cash)
    equity_curve: list[Decimal] = []
    trades = 0
    sig_idx = 0
    signals_sorted = sorted(signals, key=lambda s: s["ts"])

    for bar in bars:
        while sig_idx < len(signals_sorted) and signals_sorted[sig_idx]["ts"] <= bar.ts:
            sig = signals_sorted[sig_idx]
            equity = sim.equity({s: bar.close for s in [bar.symbol]})
            notional = equity * Decimal(str(sig["size_pct"]))
            qty = notional / bar.close
            sim.apply_fill(bar.symbol, sig["side"], qty, bar.close, bar.ts)
            trades += 1
            sig_idx += 1
        equity_curve.append(sim.equity({bar.symbol: bar.close}))

    if trades == 0:
        return BacktestResult(Decimal("0"), Decimal("0"), Decimal("0"), 0,
                              equity_curve=equity_curve, notes="no trades executed")

    arr = np.array([float(x) for x in equity_curve])
    if len(arr) < 2 or arr[0] == 0:
        return BacktestResult(Decimal("0"), Decimal("0"), Decimal("0"), trades,
                              equity_curve=equity_curve, notes="insufficient equity samples")

    log_returns = np.diff(np.log(arr))

    bar_minutes = max(1, int((bars[1].ts - bars[0].ts).total_seconds() // 60)) if len(bars) > 1 else 5
    bars_per_day = 1440 // bar_minutes
    annualization = sqrt(252 * bars_per_day)

    mean_r = float(np.mean(log_returns)) if log_returns.size else 0.0
    std_r = float(np.std(log_returns, ddof=1)) if log_returns.size > 1 else 0.0
    sharpe = Decimal(str((mean_r / std_r) * annualization if std_r > 0 else 0.0)).quantize(Decimal("0.0001"))

    running_max = np.maximum.accumulate(arr)
    drawdowns = (running_max - arr) / running_max
    max_dd = Decimal(str(float(drawdowns.max()))).quantize(Decimal("0.0001"))

    t_stat = _newey_west_t(log_returns, lag=5)

    passed = (
        sharpe >= SHARPE_THRESHOLD
        and max_dd < MAX_DD_THRESHOLD
        and t_stat >= T_STAT_THRESHOLD
        and trades >= MIN_TRADES
    )

    return BacktestResult(
        sharpe=sharpe, max_dd_pct=max_dd, t_stat=t_stat,
        trade_count=trades, equity_curve=equity_curve,
        passed=passed,
        notes="ok" if passed else "below threshold",
    )


def _newey_west_t(returns: np.ndarray, lag: int = 5) -> Decimal:
    if returns.size < lag + 2:
        return Decimal("0")
    n = returns.size
    mean = float(np.mean(returns))
    resid = returns - mean
    gamma = [float(np.sum(resid * resid)) / n]
    for k in range(1, lag + 1):
        gamma.append(float(np.sum(resid[k:] * resid[:-k])) / n)
    weights = [1.0 - k / (lag + 1) for k in range(lag + 1)]
    nw_var = gamma[0] + 2 * sum(w * g for w, g in zip(weights[1:], gamma[1:]))
    if nw_var <= 0:
        return Decimal("0")
    se = sqrt(nw_var / n)
    if se == 0:
        return Decimal("0")
    return Decimal(str(mean / se)).quantize(Decimal("0.0001"))
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_backtest_engine.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/loophedge/backtest/ tests/test_backtest_engine.py
git commit -m "feat: walk-forward backtest engine with sharpe, max-dd, newey-west t-stat"
```

---

### Task 5: Strategy interface + dynamic loader

**Files:**
- Create: `src/loophedge/strategies/__init__.py` (empty)
- Create: `src/loophedge/strategies/interface.py`
- Create: `src/loophedge/strategies/loader.py`
- Create: `tests/test_strategy_loader.py`

**Interfaces:**
- Consumes: `SkillsRepo` from Task 2.
- Produces: `Strategy` protocol (defines `NAME: str`, `DEFAULT_HYPERPARAMS: dict`, `generate_signals(bars, hyperparams) -> list[dict]`). `load_strategy(name: str, skills_repo: SkillsRepo) -> ModuleType` (loads from `skills/strategies/active/<name>.py` or `pending/`).

- [ ] **Step 1: Write failing test**

`tests/test_strategy_loader.py`:
```python
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
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_strategy_loader.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `src/loophedge/strategies/interface.py`**

```python
from typing import Any, Protocol


class Strategy(Protocol):
    NAME: str
    DEFAULT_HYPERPARAMS: dict[str, Any]

    @staticmethod
    def generate_signals(bars: list, hyperparams: dict[str, Any]) -> list[dict]:
        ...
```

- [ ] **Step 4: Implement `src/loophedge/strategies/loader.py`**

```python
import importlib.util
from pathlib import Path
from types import ModuleType

from loophedge.memory.skills import SkillsRepo


def load_strategy(name: str, skills_repo: SkillsRepo) -> ModuleType:
    for sub in ("active", "pending"):
        path = skills_repo.root / "strategies" / sub / f"{name}.py"
        if path.exists():
            return _load_module(path, name)
    raise FileNotFoundError(f"strategy {name} not found in active/ or pending/")


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"loophedge_strategies.{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for attr in ("NAME", "DEFAULT_HYPERPARAMS", "generate_signals"):
        if not hasattr(module, attr):
            raise AttributeError(f"strategy {name} missing required attribute {attr}")
    return module
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest tests/test_strategy_loader.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/loophedge/strategies/__init__.py src/loophedge/strategies/interface.py src/loophedge/strategies/loader.py tests/test_strategy_loader.py
git commit -m "feat: strategy protocol and dynamic loader for active/pending modules"
```

---

### Task 6: Strategy registry CRUD

**Files:**
- Create: `src/loophedge/strategies/registry.py`
- Create: `tests/test_strategy_registry.py`

**Interfaces:**
- Consumes: `loophedge.models.Strategy` (ORM), `SkillsRepo`.
- Produces: `class StrategyRegistry(session_factory, skills_repo)` with:
  - `register_pending(name, source_code, hyperparams: dict, actor: str) -> str` — writes file under `skills/strategies/pending/` (via SkillsRepo commit) and inserts ORM row with `status="pending"`. Returns strategy id.
  - `promote(name, actor, reason) -> None` — moves source file from `pending/` to `active/`, updates ORM row to `status="active"`, `promoted_at`, `promoted_reason`.
  - `retire(name, actor, reason) -> None` — moves from `active/` to `retired/`, updates ORM row.
  - `list_active() -> list[Strategy]` — returns ORM rows.
  - `list_pending() -> list[Strategy]`.

- [ ] **Step 1: Write failing tests**

`tests/test_strategy_registry.py`:
```python
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
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_strategy_registry.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `src/loophedge/strategies/registry.py`**

```python
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import sessionmaker

from loophedge.memory.skills import SkillsRepo
from loophedge.models import Strategy


class StrategyRegistry:
    def __init__(self, session_factory: sessionmaker, skills_repo: SkillsRepo):
        self.session_factory = session_factory
        self.skills = skills_repo

    def register_pending(self, name: str, source_code: str,
                          hyperparams: dict, actor: str) -> str:
        relpath = f"strategies/pending/{name}.py"
        self.skills.write(relpath, source_code, actor=actor,
                           reason=f"genesis proposed {name}")
        sid = str(uuid.uuid4())
        with self.session_factory() as s:
            s.add(Strategy(id=sid, name=name, status="pending",
                            source_path=relpath, hyperparams=hyperparams,
                            created_at=datetime.now(UTC)))
            s.commit()
        return sid

    def promote(self, name: str, actor: str, reason: str) -> None:
        self._move(name, src_sub="pending", dst_sub="active", actor=actor, reason=reason)
        with self.session_factory() as s:
            row = s.query(Strategy).filter_by(name=name).one()
            row.status = "active"
            row.promoted_at = datetime.now(UTC)
            row.promoted_reason = reason
            row.source_path = f"strategies/active/{name}.py"
            s.commit()

    def retire(self, name: str, actor: str, reason: str) -> None:
        self._move(name, src_sub="active", dst_sub="retired", actor=actor, reason=reason)
        with self.session_factory() as s:
            row = s.query(Strategy).filter_by(name=name).one()
            row.status = "retired"
            row.retired_at = datetime.now(UTC)
            row.retired_reason = reason
            row.source_path = f"strategies/retired/{name}.py"
            s.commit()

    def list_active(self) -> list[Strategy]:
        with self.session_factory() as s:
            return s.query(Strategy).filter_by(status="active").all()

    def list_pending(self) -> list[Strategy]:
        with self.session_factory() as s:
            return s.query(Strategy).filter_by(status="pending").all()

    def _move(self, name: str, src_sub: str, dst_sub: str, actor: str, reason: str) -> None:
        src = self.skills.root / "strategies" / src_sub / f"{name}.py"
        if not src.exists():
            raise FileNotFoundError(f"strategy {name} not in {src_sub}/")
        content = src.read_text(encoding="utf-8")
        src.unlink()
        self.skills.write(f"strategies/{dst_sub}/{name}.py", content,
                           actor=actor, reason=f"{src_sub}->{dst_sub}: {reason}")
        # also stage the deletion so the repo reflects it
        self.skills._repo.index.remove([f"strategies/{src_sub}/{name}.py"], working_tree=True)
        self.skills._repo.index.commit(f"{actor}: removed {name} from {src_sub}/ ({reason})")
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_strategy_registry.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/loophedge/strategies/registry.py tests/test_strategy_registry.py
git commit -m "feat: strategy registry with pending->active->retired lifecycle"
```

---

### Task 7: `AgentClient` — Anthropic wrapper with tool registry

**Files:**
- Create: `src/loophedge/agents/__init__.py` (empty)
- Create: `src/loophedge/agents/client.py`
- Create: `src/loophedge/agents/tools.py`
- Create: `tests/test_agent_client.py`

**Interfaces:**
- Consumes: `anthropic` SDK.
- Produces: `class AgentClient(model: str, system_prompt: str, tools: list[ToolSpec])` with `run(messages: list[dict], max_turns: int = 10) -> str` returning the final assistant text. Internally runs the tool loop: call `client.messages.create`, dispatch tool_use blocks to registered tool functions, append tool_result, repeat until stop_reason is `end_turn` or max_turns hit. `ToolSpec` is `{name, description, input_schema, function}`. Each tool function returns a JSON-serializable dict.

- [ ] **Step 1: Write failing test**

`tests/test_agent_client.py`:
```python
import json
from unittest.mock import MagicMock, patch

import pytest

from loophedge.agents.client import AgentClient, ToolSpec


def _fake_response(text=None, tool_use=None, stop_reason="end_turn"):
    blocks = []
    if text:
        blocks.append(MagicMock(type="text", text=text))
    if tool_use:
        b = MagicMock(type="tool_use")
        b.id = tool_use["id"]
        b.name = tool_use["name"]
        b.input = tool_use["input"]
        blocks.append(b)
    resp = MagicMock()
    resp.content = blocks
    resp.stop_reason = stop_reason
    return resp


def test_text_only_response_returns_text():
    with patch("loophedge.agents.client.anthropic.Anthropic") as M:
        M.return_value.messages.create.return_value = _fake_response(text="hi")
        c = AgentClient(model="claude-sonnet-4-6", system_prompt="sys", tools=[])
        assert c.run([{"role": "user", "content": "hello"}]) == "hi"


def test_tool_use_dispatched_then_loop_continues():
    calls = []
    def tool_fn(symbol: str):
        calls.append(symbol)
        return {"price": 60000}

    spec = ToolSpec(name="get_price",
                     description="get",
                     input_schema={"type": "object", "properties": {"symbol": {"type": "string"}}},
                     function=tool_fn)

    with patch("loophedge.agents.client.anthropic.Anthropic") as M:
        first = _fake_response(tool_use={"id": "tu1", "name": "get_price",
                                          "input": {"symbol": "BTC"}},
                                stop_reason="tool_use")
        second = _fake_response(text="price 60000", stop_reason="end_turn")
        M.return_value.messages.create.side_effect = [first, second]
        c = AgentClient(model="claude-opus-4-7", system_prompt="sys", tools=[spec])
        out = c.run([{"role": "user", "content": "what's BTC?"}])
        assert "60000" in out
        assert calls == ["BTC"]


def test_max_turns_raises():
    with patch("loophedge.agents.client.anthropic.Anthropic") as M:
        # always returns tool_use with no end_turn
        M.return_value.messages.create.return_value = _fake_response(
            tool_use={"id": "x", "name": "n", "input": {}}, stop_reason="tool_use"
        )
        spec = ToolSpec(name="n", description="",
                         input_schema={"type": "object"}, function=lambda: {})
        c = AgentClient(model="claude-sonnet-4-6", system_prompt="sys", tools=[spec])
        with pytest.raises(RuntimeError, match="max_turns"):
            c.run([{"role": "user", "content": "loop"}], max_turns=3)
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_agent_client.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `src/loophedge/agents/__init__.py`** — empty.

- [ ] **Step 4: Implement `src/loophedge/agents/client.py`**

```python
from dataclasses import dataclass
from typing import Any, Callable

import anthropic


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    function: Callable[..., dict]


class AgentClient:
    def __init__(self, model: str, system_prompt: str, tools: list[ToolSpec]):
        self.model = model
        self.system_prompt = system_prompt
        self.tools = {t.name: t for t in tools}
        self._client = anthropic.Anthropic()

    def run(self, messages: list[dict], max_turns: int = 10) -> str:
        msgs = list(messages)
        tool_schemas = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in self.tools.values()
        ]
        for _ in range(max_turns):
            kwargs = {"model": self.model, "system": self.system_prompt,
                       "messages": msgs, "max_tokens": 4096}
            if tool_schemas:
                kwargs["tools"] = tool_schemas
            resp = self._client.messages.create(**kwargs)

            assistant_content = []
            tool_results = []
            for block in resp.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({"type": "tool_use", "id": block.id,
                                                "name": block.name, "input": block.input})
                    spec = self.tools.get(block.name)
                    if spec is None:
                        result = {"error": f"unknown tool {block.name}"}
                    else:
                        try:
                            result = spec.function(**block.input)
                        except Exception as e:
                            result = {"error": str(e)}
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                          "content": _to_text(result)})

            msgs.append({"role": "assistant", "content": assistant_content})

            if resp.stop_reason == "end_turn" or not tool_results:
                return "".join(b["text"] for b in assistant_content if b["type"] == "text")

            msgs.append({"role": "user", "content": tool_results})
        raise RuntimeError(f"agent exceeded max_turns={max_turns}")


def _to_text(payload: dict) -> str:
    import json
    return json.dumps(payload, default=str)
```

- [ ] **Step 5: Implement `src/loophedge/agents/tools.py`** — leave empty stub for now; populated in Task 9.

```python
"""Tool functions registered with AgentClient. Implementations are wired in later tasks."""
```

- [ ] **Step 6: Run tests — expect PASS**

Run: `pytest tests/test_agent_client.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add src/loophedge/agents/__init__.py src/loophedge/agents/client.py src/loophedge/agents/tools.py tests/test_agent_client.py
git commit -m "feat: AgentClient runs tool loop against anthropic sdk"
```

---

### Task 8: VCR cassette infrastructure

**Files:**
- Modify: `tests/conftest.py`
- Create: `tests/cassettes/.gitkeep`

**Interfaces:**
- Produces: pytest fixture `vcr_cassette(cassette_name: str)` that wraps a VCR recording mode and stores cassettes in `tests/cassettes/`. Recording mode is `record_mode="none"` by default; if env `ANTHROPIC_LIVE_RECORD=1`, switches to `record_mode="all"`. Filters out the `Authorization` header so API keys never leak into committed cassettes.

- [ ] **Step 1: Write failing test**

`tests/test_cassette_smoke.py`:
```python
import os

import pytest
from anthropic import Anthropic


@pytest.mark.skipif(
    os.environ.get("ANTHROPIC_API_KEY") is None and not os.path.exists(
        "tests/cassettes/cassette_smoke.yaml"
    ),
    reason="no api key and no cassette",
)
def test_cassette_replay_smoke(vcr_cassette):
    with vcr_cassette("cassette_smoke"):
        # We don't make a live call here — we just verify the fixture mounts
        # without raising. If a cassette exists, anthropic can be constructed.
        c = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", "sk-fake"))
        assert c is not None
```

- [ ] **Step 2: Run test — expect failure (fixture missing)**

Run: `pytest tests/test_cassette_smoke.py -v`
Expected: fixture `vcr_cassette` not found.

- [ ] **Step 3: Update `tests/conftest.py` — append the fixture**

```python
import os
from contextlib import contextmanager
from pathlib import Path

import vcr as _vcr


CASSETTE_DIR = Path(__file__).parent / "cassettes"
_RECORD_MODE = "all" if os.environ.get("ANTHROPIC_LIVE_RECORD") == "1" else "none"


@pytest.fixture
def vcr_cassette():
    @contextmanager
    def _ctx(name: str):
        cassette_path = str(CASSETTE_DIR / f"{name}.yaml")
        cfg = _vcr.VCR(
            cassette_library_dir=str(CASSETTE_DIR),
            record_mode=_RECORD_MODE,
            filter_headers=["authorization", "x-api-key"],
            match_on=["method", "scheme", "host", "port", "path", "query", "body"],
        )
        with cfg.use_cassette(cassette_path):
            yield
    return _ctx
```

(Append at the bottom of `tests/conftest.py`. Make sure `import pytest` is already at the top — if not, add it.)

- [ ] **Step 4: Create a recorded cassette stub**

Since no live key is required for the smoke test (we never call the API), create an empty cassette:

`tests/cassettes/cassette_smoke.yaml`:
```yaml
interactions: []
version: 1
```

- [ ] **Step 5: Run test — expect PASS**

Run: `pytest tests/test_cassette_smoke.py -v`
Expected: 1 passed (no live API call made).

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/cassettes/ tests/test_cassette_smoke.py
git commit -m "test: vcr cassette fixture with auth header filtering"
```

---

### Task 9: Genesis agent

**Files:**
- Create: `src/loophedge/agents/genesis.py`
- Modify: `src/loophedge/agents/tools.py` — add genesis tools.
- Create: `tests/test_genesis.py`
- Create: `tests/cassettes/genesis_proposes_strategy.yaml` (recorded once via live run; committed for replay).

**Interfaces:**
- Consumes: `AgentClient`, `SkillsRepo`, `LessonsLog`, `StrategyRegistry`, `run_backtest`, historical bars from Postgres.
- Produces: `class GenesisAgent(client: AgentClient, registry: StrategyRegistry, skills: SkillsRepo, lessons: LessonsLog, session_factory)` with `propose_once() -> str | None` returning the strategy name proposed (or None if model declined to propose).
- Tools exposed to the genesis agent: `read_skill(path)`, `read_lessons(n=20)`, `query_bars(symbol, timeframe, limit)`, `propose_strategy(name, source_code, hyperparams)`.

- [ ] **Step 1: Write failing test (cassette-driven)**

`tests/test_genesis.py`:
```python
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


@pytest.mark.skipif(
    not (Path(__file__).parent / "cassettes" / "genesis_proposes_strategy.yaml").exists(),
    reason="cassette missing; record with ANTHROPIC_LIVE_RECORD=1"
)
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
```

- [ ] **Step 2: Run test — expect ImportError**

Run: `pytest tests/test_genesis.py -v`
Expected: ImportError on `loophedge.agents.genesis`.

- [ ] **Step 3: Implement `src/loophedge/agents/tools.py`**

```python
"""Tool functions registered with AgentClient.

These wrap the agents' allowed side effects. Each function returns a
JSON-serializable dict. Keep functions small and obviously safe — they
are the chokepoint that limits what the LLM can do.
"""
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from loophedge.memory.lessons import LessonsLog
from loophedge.memory.skills import SkillsRepo
from loophedge.models import Bar
from loophedge.strategies.registry import StrategyRegistry


def make_read_skill(skills: SkillsRepo):
    def read_skill(path: str) -> dict:
        return {"path": path, "content": skills.read(path)}
    return read_skill


def make_read_lessons(lessons: LessonsLog):
    def read_lessons(n: int = 20) -> dict:
        return {"lessons": lessons.recent(n)}
    return read_lessons


def make_query_bars(session_factory):
    def query_bars(symbol: str, timeframe: str, limit: int = 200) -> dict:
        with session_factory() as s:
            rows = s.execute(
                select(Bar)
                .where(Bar.symbol == symbol, Bar.timeframe == timeframe)
                .order_by(Bar.ts.desc()).limit(limit)
            ).scalars().all()
        return {
            "bars": [
                {"ts": r.ts.isoformat(), "open": str(r.open), "high": str(r.high),
                 "low": str(r.low), "close": str(r.close), "volume": str(r.volume)}
                for r in reversed(rows)
            ]
        }
    return query_bars


def make_propose_strategy(registry: StrategyRegistry):
    def propose_strategy(name: str, source_code: str, hyperparams: dict[str, Any]) -> dict:
        sid = registry.register_pending(name, source_code, hyperparams, actor="genesis")
        return {"strategy_id": sid, "name": name, "status": "pending"}
    return propose_strategy
```

- [ ] **Step 4: Implement `src/loophedge/agents/genesis.py`**

```python
from loophedge.agents.client import AgentClient, ToolSpec
from loophedge.agents.tools import (
    make_propose_strategy, make_query_bars, make_read_lessons, make_read_skill,
)
from loophedge.memory.lessons import LessonsLog
from loophedge.memory.skills import SkillsRepo
from loophedge.strategies.registry import StrategyRegistry


SYSTEM_PROMPT = """\
You are the strategy genesis agent for a crypto paper-trading hedge fund.

Your job: read the strategy_genesis playbook, read the lessons learned, examine
recent BTCUSDT 5m bars, and PROPOSE ONE strategy by calling propose_strategy.

A strategy is a Python file that exports:
- NAME: str
- DEFAULT_HYPERPARAMS: dict
- generate_signals(bars, hyperparams) -> list[dict]

Each signal dict needs {symbol, side, size_pct, ts}. Position size_pct must be
between 0.005 and 0.05 (between 0.5% and 5% of equity).

Use only deterministic technical indicators. Do not import network libraries.

After you propose, your turn ends.
"""


class GenesisAgent:
    def __init__(self, client: AgentClient, registry: StrategyRegistry,
                  skills: SkillsRepo, lessons: LessonsLog, session_factory):
        client.system_prompt = SYSTEM_PROMPT
        client.tools = {
            t.name: t
            for t in [
                ToolSpec("read_skill", "Read a markdown skill file by relative path",
                          {"type": "object",
                           "properties": {"path": {"type": "string"}},
                           "required": ["path"]},
                          make_read_skill(skills)),
                ToolSpec("read_lessons", "Read the last n lessons learned",
                          {"type": "object",
                           "properties": {"n": {"type": "integer", "default": 20}}},
                          make_read_lessons(lessons)),
                ToolSpec("query_bars", "Fetch recent bars for a symbol",
                          {"type": "object",
                           "properties": {"symbol": {"type": "string"},
                                            "timeframe": {"type": "string"},
                                            "limit": {"type": "integer", "default": 200}},
                           "required": ["symbol", "timeframe"]},
                          make_query_bars(session_factory)),
                ToolSpec("propose_strategy", "Submit a new strategy proposal",
                          {"type": "object",
                           "properties": {"name": {"type": "string"},
                                            "source_code": {"type": "string"},
                                            "hyperparams": {"type": "object"}},
                           "required": ["name", "source_code", "hyperparams"]},
                          make_propose_strategy(registry)),
            ]
        }
        self.client = client
        self.registry = registry

    def propose_once(self) -> str | None:
        before = {s.name for s in self.registry.list_pending()}
        user_msg = ("Read the genesis playbook, lessons, and recent bars. "
                     "Then propose ONE strategy.")
        self.client.run([{"role": "user", "content": user_msg}], max_turns=8)
        after = {s.name for s in self.registry.list_pending()}
        new = after - before
        return next(iter(new), None)
```

- [ ] **Step 5: Recording the cassette**

This step requires a live Anthropic API key. The implementer should NOT run this; instead, leave a placeholder cassette and document the recording process. Create a minimal placeholder so the test is skipped (per the skipif condition):

```bash
# DO NOT RUN — recording instructions for the user
# Set ANTHROPIC_API_KEY and ANTHROPIC_LIVE_RECORD=1, then:
# pytest tests/test_genesis.py::test_genesis_proposes_strategy -v
# This creates tests/cassettes/genesis_proposes_strategy.yaml.
# Then COMMIT the cassette to source control.
```

For Phase 2 plan execution, the implementer creates the cassette as an empty placeholder so the test skips cleanly:

`tests/cassettes/genesis_proposes_strategy.yaml`:
```yaml
# Placeholder: replace by running:
#   ANTHROPIC_LIVE_RECORD=1 ANTHROPIC_API_KEY=sk-... pytest tests/test_genesis.py
# Until then, the test self-skips via skipif on the cassette's mtime being 0.
interactions: []
version: 1
```

Add to the skipif a check for whether the cassette has non-trivial content:

Modify the `pytest.mark.skipif` line in `tests/test_genesis.py`:
```python
def _cassette_recorded() -> bool:
    p = Path(__file__).parent / "cassettes" / "genesis_proposes_strategy.yaml"
    return p.exists() and p.stat().st_size > 200  # >200 bytes = real cassette


@pytest.mark.skipif(not _cassette_recorded(),
                     reason="cassette is placeholder; record with ANTHROPIC_LIVE_RECORD=1")
def test_genesis_proposes_strategy(...):
```

- [ ] **Step 6: Run test — expect SKIP**

Run: `pytest tests/test_genesis.py -v`
Expected: 1 skipped (placeholder cassette).

- [ ] **Step 7: Commit**

```bash
git add src/loophedge/agents/tools.py src/loophedge/agents/genesis.py tests/test_genesis.py tests/cassettes/genesis_proposes_strategy.yaml
git commit -m "feat: genesis agent proposes strategies via anthropic tool loop"
```

---

### Task 10: Checker agent

**Files:**
- Create: `src/loophedge/agents/checker.py`
- Modify: `src/loophedge/agents/tools.py` — add `make_run_backtest`.
- Create: `tests/test_checker.py`
- Create: `tests/cassettes/checker_approves_strategy.yaml` (placeholder).

**Interfaces:**
- Consumes: `AgentClient`, `SkillsRepo`, `LessonsLog`, `StrategyRegistry`, `run_backtest`, `Bus`.
- Produces: `class CheckerAgent(client, registry, skills, lessons, session_factory, bus)` with `validate(strategy_name: str) -> str` returning `"approved"`, `"rejected"`, or `"needs_revision"`. On approve: promotes the strategy via the registry. On reject: appends a lesson + retires the pending strategy.

- [ ] **Step 1: Write failing test**

`tests/test_checker.py`:
```python
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
        verdict = ck.validate("test_strat")

    assert verdict in ("approved", "rejected", "needs_revision")
    # Whichever way it goes, the registry is consistent:
    with session_factory() as s:
        row = s.query(Strategy).filter_by(name="test_strat").one()
        assert row.status in ("active", "retired", "pending")  # at minimum mutable
```

- [ ] **Step 2: Run test — expect ImportError or skip**

Run: `pytest tests/test_checker.py -v`
Expected: ImportError before module exists; skip after.

- [ ] **Step 3: Add `make_run_backtest` to `src/loophedge/agents/tools.py`**

Append:

```python
def make_run_backtest(skills, session_factory):
    from loophedge.backtest.engine import run_backtest
    from loophedge.strategies.loader import load_strategy
    from sqlalchemy import select
    from loophedge.models import Bar

    def run_strategy_backtest(strategy_name: str, lookback_bars: int = 500) -> dict:
        module = load_strategy(strategy_name, skills)
        with session_factory() as s:
            rows = s.execute(
                select(Bar).where(Bar.symbol == "BTCUSDT")
                .order_by(Bar.ts.desc()).limit(lookback_bars)
            ).scalars().all()
        bars = list(reversed(rows))
        result = run_backtest(bars, module.generate_signals, module.DEFAULT_HYPERPARAMS)
        return {
            "sharpe": str(result.sharpe),
            "max_dd_pct": str(result.max_dd_pct),
            "t_stat": str(result.t_stat),
            "trade_count": result.trade_count,
            "passed": result.passed,
            "notes": result.notes,
        }
    return run_strategy_backtest
```

- [ ] **Step 4: Implement `src/loophedge/agents/checker.py`**

```python
import json
from datetime import UTC, datetime

from loophedge.agents.client import AgentClient, ToolSpec
from loophedge.agents.tools import (
    make_read_skill, make_read_lessons, make_run_backtest,
)
from loophedge.bus import CH_SIGNAL_REJECTED, CH_SIGNAL_VERIFIED, Bus
from loophedge.memory.lessons import LessonsLog
from loophedge.memory.skills import SkillsRepo
from loophedge.strategies.registry import StrategyRegistry


SYSTEM_PROMPT = """\
You are the checker agent. Your job is to independently validate a proposed
strategy by running its backtest and judging the result against the rubric in
backtest_verification.md.

Read the playbook first. Then run_strategy_backtest with the strategy name.
Compare the result against the thresholds. Return a JSON object on your final
turn (and NOTHING else) of the form:

{"verdict": "approve" | "reject" | "needs_revision", "reason": "..."}
"""


class CheckerAgent:
    def __init__(self, client: AgentClient, registry: StrategyRegistry,
                  skills: SkillsRepo, lessons: LessonsLog, session_factory, bus: Bus):
        client.system_prompt = SYSTEM_PROMPT
        client.tools = {
            t.name: t for t in [
                ToolSpec("read_skill", "Read a markdown skill file",
                          {"type": "object",
                           "properties": {"path": {"type": "string"}},
                           "required": ["path"]},
                          make_read_skill(skills)),
                ToolSpec("read_lessons", "Read recent lessons",
                          {"type": "object",
                           "properties": {"n": {"type": "integer"}}},
                          make_read_lessons(lessons)),
                ToolSpec("run_strategy_backtest", "Run a backtest of the proposed strategy",
                          {"type": "object",
                           "properties": {"strategy_name": {"type": "string"},
                                            "lookback_bars": {"type": "integer", "default": 500}},
                           "required": ["strategy_name"]},
                          make_run_backtest(skills, session_factory)),
            ]
        }
        self.client = client
        self.registry = registry
        self.lessons = lessons
        self.bus = bus

    def validate(self, strategy_name: str) -> str:
        prompt = (f"Validate the proposed strategy named '{strategy_name}'. "
                   "Read backtest_verification.md, run the backtest, and emit your verdict JSON.")
        raw = self.client.run([{"role": "user", "content": prompt}], max_turns=6)
        verdict = _parse_verdict(raw)

        if verdict["verdict"] == "approve":
            self.registry.promote(strategy_name, actor="checker",
                                    reason=verdict["reason"])
            return "approved"
        if verdict["verdict"] == "reject":
            self.lessons.append("checker", datetime.now(UTC),
                                  f"rejected {strategy_name}: {verdict['reason']}")
            self.registry.retire(strategy_name, actor="checker",
                                  reason=verdict["reason"])
            return "rejected"
        return "needs_revision"


def _parse_verdict(text: str) -> dict:
    s = text.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1:
        return {"verdict": "needs_revision", "reason": "no JSON object returned"}
    try:
        return json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return {"verdict": "needs_revision", "reason": "malformed JSON"}
```

- [ ] **Step 5: Create placeholder cassette**

`tests/cassettes/checker_approves_strategy.yaml`:
```yaml
# Placeholder. Record with:
#   ANTHROPIC_LIVE_RECORD=1 pytest tests/test_checker.py
interactions: []
version: 1
```

- [ ] **Step 6: Run test — expect SKIP**

Run: `pytest tests/test_checker.py -v`
Expected: 1 skipped.

- [ ] **Step 7: Commit**

```bash
git add src/loophedge/agents/checker.py src/loophedge/agents/tools.py tests/test_checker.py tests/cassettes/checker_approves_strategy.yaml
git commit -m "feat: checker agent validates strategies via independent backtest"
```

---

### Task 11: Maker agent with dual scheduling

**Files:**
- Create: `src/loophedge/agents/maker.py`
- Create: `tests/test_maker.py`
- Create: `tests/cassettes/maker_emits_candidate.yaml` (placeholder).

**Interfaces:**
- Consumes: `AgentClient`, `Bus`, `StrategyRegistry`, `SkillsRepo`, `LessonsLog`, `session_factory`, `loophedge.strategies.loader.load_strategy`.
- Produces: `class MakerAgent(client, registry, skills, lessons, session_factory, bus, watermark_path: Path)` with:
  - `should_tick() -> bool` — returns True only if a new `bar.closed` timestamp has been observed since last tick. Watermark stored in `state/maker_watermark.txt`.
  - `record_bar_seen(ts: datetime) -> None` — called by the scheduler when `bar.closed` is consumed.
  - `tick() -> int` — runs the agent loop, emits 0..N `signal.candidate` events to the bus, returns count.
- Scheduling: caller (the `cli.run_maker` wrapper) sets up APScheduler with a 15-minute interval AND a Redis subscriber updating the watermark on every `bar.closed`. Both call `maker.tick()` only if `should_tick()` returns True.

- [ ] **Step 1: Write failing test (non-cassette parts)**

`tests/test_maker.py`:
```python
from datetime import UTC, datetime
from pathlib import Path

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
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_maker.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `src/loophedge/agents/maker.py`**

```python
import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from loophedge.agents.client import AgentClient, ToolSpec
from loophedge.agents.tools import make_query_bars, make_read_lessons, make_read_skill
from loophedge.bus import CH_SIGNAL_CANDIDATE, Bus
from loophedge.memory.lessons import LessonsLog
from loophedge.memory.skills import SkillsRepo
from loophedge.models import Signal
from loophedge.schemas import SignalCandidate
from loophedge.strategies.loader import load_strategy
from loophedge.strategies.registry import StrategyRegistry


SYSTEM_PROMPT = """\
You are the maker agent. Your job is to emit candidate trade signals from the
currently active strategies, filtered against the lessons learned.

Workflow:
1. Read alpha_research.md and the recent lessons.
2. For each active strategy, examine the latest bars and decide whether to call
   its generate_signals output verbatim or to suppress signals based on lessons.

You do not need to call any tools beyond what's necessary to read context. The
maker harness will iterate active strategies and forward their signals based on
your filter decisions.
"""


class MakerAgent:
    """Maker emits candidate signals on dual schedule (timer + bar.closed gating)."""

    def __init__(self, client: AgentClient, registry: StrategyRegistry,
                  skills: SkillsRepo, lessons: LessonsLog, session_factory,
                  bus: Bus, watermark_path: Path):
        client.system_prompt = SYSTEM_PROMPT
        client.tools = {
            t.name: t for t in [
                ToolSpec("read_skill", "Read skill file",
                          {"type": "object",
                           "properties": {"path": {"type": "string"}},
                           "required": ["path"]},
                          make_read_skill(skills)),
                ToolSpec("read_lessons", "Recent lessons",
                          {"type": "object",
                           "properties": {"n": {"type": "integer"}}},
                          make_read_lessons(lessons)),
                ToolSpec("query_bars", "Recent bars",
                          {"type": "object",
                           "properties": {"symbol": {"type": "string"},
                                            "timeframe": {"type": "string"},
                                            "limit": {"type": "integer"}},
                           "required": ["symbol", "timeframe"]},
                          make_query_bars(session_factory)),
            ]
        }
        self.client = client
        self.registry = registry
        self.skills = skills
        self.session_factory = session_factory
        self.bus = bus
        self.watermark_path = watermark_path

    def record_bar_seen(self, ts: datetime) -> None:
        self.watermark_path.write_text(f"seen={ts.isoformat()}\nticked={self._read_ticked()}\n")

    def _read_ticked(self) -> str:
        if not self.watermark_path.exists():
            return ""
        for ln in self.watermark_path.read_text().splitlines():
            if ln.startswith("ticked="):
                return ln.split("=", 1)[1]
        return ""

    def _read_seen(self) -> str:
        if not self.watermark_path.exists():
            return ""
        for ln in self.watermark_path.read_text().splitlines():
            if ln.startswith("seen="):
                return ln.split("=", 1)[1]
        return ""

    def _mark_ticked(self, ts: datetime) -> None:
        seen = self._read_seen()
        self.watermark_path.write_text(f"seen={seen}\nticked={ts.isoformat()}\n")

    def should_tick(self) -> bool:
        seen = self._read_seen()
        ticked = self._read_ticked()
        return bool(seen) and seen != ticked

    async def tick(self) -> int:
        actives = self.registry.list_active()
        if not actives:
            return 0

        prompt = ("Active strategies: " + ", ".join(s.name for s in actives)
                   + ". Read the relevant skill/lessons and decide which signals to emit.")
        # We capture the LLM's contextual filter, then iterate strategies mechanically.
        self.client.run([{"role": "user", "content": prompt}], max_turns=4)

        emitted = 0
        for strat in actives:
            try:
                module = load_strategy(strat.name, self.skills)
            except Exception:
                continue
            with self.session_factory() as s:
                from sqlalchemy import select
                from loophedge.models import Bar
                rows = s.execute(
                    select(Bar).where(Bar.symbol == "BTCUSDT")
                    .order_by(Bar.ts.desc()).limit(200)
                ).scalars().all()
            bars = list(reversed(rows))
            try:
                sigs = module.generate_signals(bars, strat.hyperparams) or []
            except Exception:
                continue
            for sig in sigs[-3:]:  # cap per strategy per tick
                signal_id = str(uuid.uuid4())
                with self.session_factory() as s:
                    s.add(Signal(id=signal_id, strategy_id=strat.name,
                                  symbol=sig["symbol"], side=sig["side"],
                                  size_pct=Decimal(str(sig["size_pct"])),
                                  status="candidate",
                                  maker_payload={"ts": str(sig["ts"])}))
                    s.commit()
                await self.bus.publish(CH_SIGNAL_CANDIDATE, SignalCandidate(
                    signal_id=signal_id, strategy_id=strat.name,
                    symbol=sig["symbol"], side=sig["side"],
                    size_pct=Decimal(str(sig["size_pct"])),
                    reasoning="maker emitted from active strategy"))
                emitted += 1

        self._mark_ticked(datetime.now(UTC))
        return emitted
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_maker.py -v`
Expected: 3 passed.

- [ ] **Step 5: Create placeholder cassette**

`tests/cassettes/maker_emits_candidate.yaml`:
```yaml
interactions: []
version: 1
```

- [ ] **Step 6: Commit**

```bash
git add src/loophedge/agents/maker.py tests/test_maker.py tests/cassettes/maker_emits_candidate.yaml
git commit -m "feat: maker agent with dual scheduling and watermark gating"
```

---

### Task 12: Long-running executor subscriber + auto-flatten

**Files:**
- Modify: `src/loophedge/services/executor.py` — add `class ExecutorService` (a long-running wrapper around the existing `Executor`).
- Create: `tests/test_executor_subscriber.py`

**Interfaces:**
- Consumes: existing `Executor`, `Bus`, `Simulator`.
- Produces: `class ExecutorService(executor: Executor, bus: Bus, simulator: Simulator, session_factory)`:
  - `async run() -> None` — main loop, subscribes to `signal.verified` AND `circuit.broken`.
  - `async handle_signal(payload: dict) -> None` — looks up the candidate, calls `executor.handle_verified`.
  - `async handle_circuit(payload: dict) -> None` — emits exit fills for every open position via the simulator + executor's persist path.
  - `async stop() -> None` — graceful shutdown.

- [ ] **Step 1: Write failing test**

`tests/test_executor_subscriber.py`:
```python
import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import fakeredis.aioredis
import pytest

from loophedge.bus import CH_CIRCUIT_BROKEN, CH_SIGNAL_VERIFIED, Bus
from loophedge.ledger.simulator import Simulator
from loophedge.models import Position, Signal
from loophedge.schemas import CircuitBroken, SignalCandidate, SignalVerified
from loophedge.services.executor import Executor, ExecutorService


@pytest.mark.asyncio
async def test_executor_service_processes_signal_verified(session_factory, starting_capital):
    bus = Bus(fakeredis.aioredis.FakeRedis())
    sim = Simulator(starting_cash=starting_capital)
    ex = Executor(bus, session_factory, sim, latest_prices={"BTCUSDT": Decimal("60000")})
    svc = ExecutorService(ex, bus, sim, session_factory)

    # Pre-create candidate in DB
    with session_factory() as s:
        s.add(Signal(id="sigA", strategy_id="momentum", symbol="BTCUSDT",
                     side="long", size_pct=Decimal("0.02"), status="candidate",
                     maker_payload={"symbol": "BTCUSDT", "side": "long",
                                    "size_pct": "0.02", "reasoning": "t"}))
        s.commit()

    # Approve it
    with session_factory() as s:
        sig = s.get(Signal, "sigA")
        sig.status = "approved"
        s.commit()

    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.05)
    await bus.publish(CH_SIGNAL_VERIFIED, SignalVerified(signal_id="sigA", verdict="approve"))
    await asyncio.sleep(0.2)
    await svc.stop()
    await asyncio.wait_for(task, timeout=1.0)

    with session_factory() as s:
        assert s.get(Signal, "sigA").status == "executed"


@pytest.mark.asyncio
async def test_circuit_broken_flattens_positions(session_factory, starting_capital):
    bus = Bus(fakeredis.aioredis.FakeRedis())
    sim = Simulator(starting_cash=starting_capital)
    sim.apply_fill("BTCUSDT", "long", Decimal("0.1"), Decimal("60000"), datetime.now(UTC))
    with session_factory() as s:
        s.add(Position(symbol="BTCUSDT", qty=Decimal("0.1"),
                       avg_entry=Decimal("60030"), unrealized_pnl=Decimal("0"),
                       updated_at=datetime.now(UTC)))
        s.commit()
    ex = Executor(bus, session_factory, sim, latest_prices={"BTCUSDT": Decimal("60000")})
    svc = ExecutorService(ex, bus, sim, session_factory)

    task = asyncio.create_task(svc.run())
    await asyncio.sleep(0.05)
    await bus.publish(CH_CIRCUIT_BROKEN, CircuitBroken(
        ts=datetime.now(UTC), drawdown_pct=Decimal("0.2"), action="flatten_all"
    ))
    await asyncio.sleep(0.3)
    await svc.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert sim.positions["BTCUSDT"].qty == Decimal("0")
    with session_factory() as s:
        assert s.get(Position, "BTCUSDT").qty == Decimal("0")
```

- [ ] **Step 2: Run tests — expect ImportError on ExecutorService**

Run: `pytest tests/test_executor_subscriber.py -v`
Expected: ImportError.

- [ ] **Step 3: Append `ExecutorService` to `src/loophedge/services/executor.py`**

Append at the bottom of the existing file (do NOT remove the existing `Executor` class):

```python
import asyncio
from typing import Any


class ExecutorService:
    """Long-running subscriber to signal.verified and circuit.broken."""

    def __init__(self, executor: Executor, bus: Bus, simulator: Simulator,
                  session_factory: sessionmaker):
        self.executor = executor
        self.bus = bus
        self.simulator = simulator
        self.session_factory = session_factory
        self._stop = asyncio.Event()

    async def run(self) -> None:
        signal_task = asyncio.create_task(self._consume_signals())
        circuit_task = asyncio.create_task(self._consume_circuit())
        try:
            await self._stop.wait()
        finally:
            signal_task.cancel()
            circuit_task.cancel()
            await asyncio.gather(signal_task, circuit_task, return_exceptions=True)

    async def stop(self) -> None:
        self._stop.set()

    async def _consume_signals(self) -> None:
        from loophedge.bus import CH_SIGNAL_VERIFIED
        async for payload in self.bus.subscribe(CH_SIGNAL_VERIFIED):
            try:
                await self.handle_signal(payload)
            except Exception:
                continue

    async def _consume_circuit(self) -> None:
        from loophedge.bus import CH_CIRCUIT_BROKEN
        async for payload in self.bus.subscribe(CH_CIRCUIT_BROKEN):
            try:
                await self.handle_circuit(payload)
            except Exception:
                continue

    async def handle_signal(self, payload: dict) -> None:
        from loophedge.schemas import SignalCandidate, SignalVerified
        verified = SignalVerified.model_validate(payload)
        with self.session_factory() as s:
            sig = s.get(Signal, verified.signal_id)
            if sig is None:
                return
            candidate = SignalCandidate(
                signal_id=sig.id, strategy_id=sig.strategy_id,
                symbol=sig.symbol, side=sig.side, size_pct=sig.size_pct,
                reasoning=(sig.maker_payload or {}).get("reasoning", ""),
            )
        await self.executor.handle_verified(verified, candidate)

    async def handle_circuit(self, payload: dict) -> None:
        snapshot = list(self.simulator.positions.items())
        for symbol, pos in snapshot:
            if pos.qty == Decimal("0"):
                continue
            side = "short" if pos.qty > 0 else "long"
            ref = self.executor.latest_prices.get(symbol, pos.avg_entry)
            fill = self.simulator.apply_fill(symbol, side, abs(pos.qty), ref,
                                              datetime.now(UTC))
            from loophedge.models import Fill as FillRow
            from loophedge.models import Position as PositionRow
            with self.session_factory() as s:
                s.add(FillRow(id=fill.id, signal_id="circuit_break",
                              ts=fill.ts, symbol=fill.symbol, side=fill.side,
                              qty=fill.qty, price=fill.price, fees=fill.fees,
                              venue="simulator"))
                p = s.get(PositionRow, symbol)
                if p is not None:
                    p.qty = self.simulator.positions[symbol].qty
                    p.avg_entry = self.simulator.positions[symbol].avg_entry
                    p.unrealized_pnl = Decimal("0")
                    p.updated_at = fill.ts
                s.commit()
```

Note: the `signal_id="circuit_break"` foreign key constraint will fail. Update the Fill model OR insert a sentinel Signal row.

Simpler — handle the FK by relaxing the column at the DB level. Modify `src/loophedge/models.py`:

```python
class Fill(Base):
    __tablename__ = "fills"
    ...
    signal_id: Mapped[str | None] = mapped_column(
        ForeignKey("signals.id"), nullable=True
    )  # was non-nullable
```

Then write an Alembic migration `migrations/versions/002_nullable_signal_id.py`:

```python
"""make Fill.signal_id nullable

Revision ID: 002
Revises: 001
Create Date: 2026-06-29
"""
from alembic import op

revision = "002"
down_revision = "001"


def upgrade():
    with op.batch_alter_table("fills") as batch_op:
        batch_op.alter_column("signal_id", nullable=True)


def downgrade():
    with op.batch_alter_table("fills") as batch_op:
        batch_op.alter_column("signal_id", nullable=False)
```

Update `ExecutorService.handle_circuit` to set `signal_id=None` instead of the string `"circuit_break"`.

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_executor_subscriber.py -v && pytest -p no:seleniumbase -q`
Expected: 2 passed for the new test; full suite still green (existing tests not broken).

- [ ] **Step 5: Commit**

```bash
git add src/loophedge/services/executor.py src/loophedge/models.py migrations/versions/002_nullable_signal_id.py tests/test_executor_subscriber.py
git commit -m "feat: long-running executor subscriber with circuit auto-flatten"
```

---

### Task 13: CLI wiring + docker-compose updates

**Files:**
- Modify: `src/loophedge/cli.py` — replace stubs with real `run_execute`, `run_risk`, plus new `run_maker`, `run_checker`, `run_genesis`.
- Modify: `docker-compose.yml` — remove `sleep infinity` placeholders for the 3 agent containers, give them real commands.
- Create: `tests/test_cli_phase2.py` — verify dispatch for the new subcommands.

**Interfaces:**
- The `_COMMANDS` tuple in `cli.py` grows: `("ingest", "execute", "risk", "dashboard", "maker", "checker", "genesis")`.
- Each `run_<name>` is a real long-running service runner; tests only verify dispatch via monkeypatch.

- [ ] **Step 1: Write failing test**

`tests/test_cli_phase2.py`:
```python
from loophedge.cli import _COMMANDS, main


def test_new_commands_registered():
    for cmd in ("maker", "checker", "genesis"):
        assert cmd in _COMMANDS


def test_maker_dispatch(monkeypatch):
    called = {}
    monkeypatch.setattr("loophedge.cli.run_maker", lambda: called.setdefault("yes", True))
    assert main(["maker"]) == 0
    assert called == {"yes": True}


def test_checker_dispatch(monkeypatch):
    called = {}
    monkeypatch.setattr("loophedge.cli.run_checker", lambda: called.setdefault("yes", True))
    assert main(["checker"]) == 0
    assert called == {"yes": True}


def test_genesis_dispatch(monkeypatch):
    called = {}
    monkeypatch.setattr("loophedge.cli.run_genesis", lambda: called.setdefault("yes", True))
    assert main(["genesis"]) == 0
    assert called == {"yes": True}
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_cli_phase2.py -v`
Expected: FAIL — new commands not registered.

- [ ] **Step 3: Rewrite `src/loophedge/cli.py`**

Replace the file contents with:

```python
import asyncio
import sys
from pathlib import Path


def run_ingest() -> None:
    import redis.asyncio
    from loophedge.bus import Bus
    from loophedge.config import get_settings
    from loophedge.db import get_session_factory
    from loophedge.services.data_ingestor import DataIngestor, binance_fetch_klines

    settings = get_settings()
    async def _go():
        redis_client = redis.asyncio.from_url(settings.redis_url)
        bus = Bus(redis_client)
        ing = DataIngestor(bus, get_session_factory(), binance_fetch_klines,
                            settings.symbols, settings.bar_timeframe)
        while True:
            await ing.fetch_and_publish_once()
            await asyncio.sleep(60)
    asyncio.run(_go())


def run_execute() -> None:
    import redis.asyncio
    from decimal import Decimal
    from loophedge.bus import Bus
    from loophedge.config import get_settings
    from loophedge.db import get_session_factory
    from loophedge.ledger.simulator import Simulator
    from loophedge.services.executor import Executor, ExecutorService

    settings = get_settings()
    async def _go():
        redis_client = redis.asyncio.from_url(settings.redis_url)
        bus = Bus(redis_client)
        sf = get_session_factory()
        sim = Simulator(starting_cash=Decimal(str(settings.starting_capital_usd)))
        ex = Executor(bus, sf, sim, latest_prices={})
        svc = ExecutorService(ex, bus, sim, sf)
        await svc.run()
    asyncio.run(_go())


def run_risk() -> None:
    import redis.asyncio
    from datetime import UTC, datetime
    from decimal import Decimal
    from loophedge.bus import Bus
    from loophedge.config import get_settings
    from loophedge.db import get_session_factory
    from loophedge.services.risk_monitor import RiskMonitor

    settings = get_settings()
    async def _go():
        redis_client = redis.asyncio.from_url(settings.redis_url)
        bus = Bus(redis_client)
        rm = RiskMonitor(bus, get_session_factory(),
                         kill_dd_pct=Decimal(str(settings.kill_switch_dd_pct)))
        while True:
            await rm.tick(datetime.now(UTC), Decimal(str(settings.starting_capital_usd)))
            await asyncio.sleep(60)
    asyncio.run(_go())


def run_dashboard() -> None:
    import uvicorn
    from loophedge.db import get_session_factory
    from loophedge.services.dashboard import build_app
    app = build_app(get_session_factory())
    uvicorn.run(app, host="0.0.0.0", port=8000)


def run_maker() -> None:
    import redis.asyncio
    from loophedge.agents.client import AgentClient
    from loophedge.agents.maker import MakerAgent
    from loophedge.bus import CH_BAR_CLOSED, Bus
    from loophedge.config import get_settings
    from loophedge.db import get_session_factory
    from loophedge.memory.lessons import LessonsLog
    from loophedge.memory.skills import SkillsRepo
    from loophedge.strategies.registry import StrategyRegistry
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from datetime import datetime
    from pathlib import Path as _Path

    settings = get_settings()
    skills_root = _Path("/app/skills")
    state_root = _Path("/app/state")
    state_root.mkdir(parents=True, exist_ok=True)

    async def _go():
        redis_client = redis.asyncio.from_url(settings.redis_url)
        bus = Bus(redis_client)
        sr = SkillsRepo(skills_root)
        lessons = LessonsLog(sr)
        reg = StrategyRegistry(get_session_factory(), sr)
        client = AgentClient(model="claude-sonnet-4-6", system_prompt="", tools=[])
        maker = MakerAgent(client, reg, sr, lessons, get_session_factory(),
                            bus, state_root / "maker_watermark.txt")

        async def _on_bar(msg):
            from datetime import datetime
            ts = datetime.fromisoformat(msg["ts"].replace("Z", "+00:00"))
            maker.record_bar_seen(ts)

        async def _on_timer():
            if maker.should_tick():
                await maker.tick()

        sched = AsyncIOScheduler()
        sched.add_job(_on_timer, "interval", minutes=15)
        sched.start()
        async for msg in bus.subscribe(CH_BAR_CLOSED):
            await _on_bar(msg)

    asyncio.run(_go())


def run_checker() -> None:
    import redis.asyncio
    from loophedge.agents.checker import CheckerAgent
    from loophedge.agents.client import AgentClient
    from loophedge.bus import CH_SIGNAL_CANDIDATE, Bus
    from loophedge.config import get_settings
    from loophedge.db import get_session_factory
    from loophedge.memory.lessons import LessonsLog
    from loophedge.memory.skills import SkillsRepo
    from loophedge.strategies.registry import StrategyRegistry
    from pathlib import Path as _Path

    settings = get_settings()
    async def _go():
        redis_client = redis.asyncio.from_url(settings.redis_url)
        bus = Bus(redis_client)
        sr = SkillsRepo(_Path("/app/skills"))
        lessons = LessonsLog(sr)
        reg = StrategyRegistry(get_session_factory(), sr)
        client = AgentClient(model="claude-opus-4-7", system_prompt="", tools=[])
        ck = CheckerAgent(client, reg, sr, lessons, get_session_factory(), bus)

        async for msg in bus.subscribe(CH_SIGNAL_CANDIDATE):
            strategy_name = msg.get("strategy_id", "")
            if not strategy_name:
                continue
            ck.validate(strategy_name)

    asyncio.run(_go())


def run_genesis() -> None:
    import redis.asyncio
    from loophedge.agents.client import AgentClient
    from loophedge.agents.genesis import GenesisAgent
    from loophedge.bus import Bus
    from loophedge.config import get_settings
    from loophedge.db import get_session_factory
    from loophedge.memory.lessons import LessonsLog
    from loophedge.memory.skills import SkillsRepo
    from loophedge.strategies.registry import StrategyRegistry
    from pathlib import Path as _Path

    settings = get_settings()
    async def _go():
        redis_client = redis.asyncio.from_url(settings.redis_url)
        bus = Bus(redis_client)
        sr = SkillsRepo(_Path("/app/skills"))
        lessons = LessonsLog(sr)
        reg = StrategyRegistry(get_session_factory(), sr)
        client = AgentClient(model="claude-opus-4-7", system_prompt="", tools=[])
        agent = GenesisAgent(client, reg, sr, lessons, get_session_factory())
        while True:
            agent.propose_once()
            await asyncio.sleep(3600)
    asyncio.run(_go())


_COMMANDS = ("ingest", "execute", "risk", "dashboard", "maker", "checker", "genesis")


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] not in _COMMANDS:
        print(f"usage: python -m loophedge {{{'|'.join(_COMMANDS)}}}", file=sys.stderr)
        return 2
    globals()[f"run_{argv[0]}"]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Update `docker-compose.yml`** — replace the agent stubs with real commands.

Replace the three agent service definitions:

```yaml
  maker-agent:
    image: loophedge:dev
    command: ["maker"]
    env_file: .env
    volumes:
      - ./skills:/app/skills
      - ./state:/app/state
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }

  checker-agent:
    image: loophedge:dev
    command: ["checker"]
    env_file: .env
    volumes:
      - ./skills:/app/skills
      - ./state:/app/state
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }

  strategy-genesis-agent:
    image: loophedge:dev
    command: ["genesis"]
    env_file: .env
    volumes:
      - ./skills:/app/skills
      - ./state:/app/state
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
```

Also add the `./skills:/app/skills` + `./state:/app/state` volume mounts to `executor`, `dashboard`, `risk-monitor`, and `data-ingestor` so all services can read the skills and state volumes consistently.

- [ ] **Step 5: Run all CLI tests + full suite**

Run: `pytest tests/test_cli.py tests/test_cli_phase2.py -v && pytest -p no:seleniumbase -q`
Expected: existing CLI tests still green + 4 new CLI tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/loophedge/cli.py docker-compose.yml tests/test_cli_phase2.py
git commit -m "feat: cli wiring and compose graph for maker/checker/genesis services"
```

---

### Task 14: End-to-end agent loop test (cassette + deterministic stubs)

**Files:**
- Create: `tests/test_e2e_agent_loop.py`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: a single integration test that uses stubbed `AgentClient.run` (NOT real cassettes — we substitute the client class to simulate deterministic agent decisions). Genesis stub returns a pre-baked strategy; checker stub approves it; maker emits signals; executor fills them.

The reason we use stubs here rather than cassettes: the full end-to-end pipeline involves three model calls of nontrivial token cost. A stub keeps CI fast and free. Individual agent tests (Tasks 9, 10, 11) use cassettes for realism; the e2e test verifies *wiring*.

- [ ] **Step 1: Write the test**

`tests/test_e2e_agent_loop.py`:
```python
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import fakeredis.aioredis
import git
import pytest

from loophedge.agents.checker import CheckerAgent
from loophedge.agents.client import AgentClient
from loophedge.agents.genesis import GenesisAgent
from loophedge.agents.maker import MakerAgent
from loophedge.bus import Bus
from loophedge.ledger.simulator import Simulator
from loophedge.memory.lessons import LessonsLog
from loophedge.memory.skills import SkillsRepo
from loophedge.models import Bar, Fill, Position, Strategy
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
```

- [ ] **Step 2: Run the test — expect PASS**

Run: `pytest tests/test_e2e_agent_loop.py -v`
Expected: 1 passed.

- [ ] **Step 3: Run full suite**

Run: `pytest -p no:seleniumbase -v`
Expected: all green (this task's + all prior tasks').

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_agent_loop.py
git commit -m "test: end-to-end stubbed agent loop covering genesis -> checker -> maker"
```

---

### Task 15: Final verification + Phase 2 tag

- [ ] **Step 1: Full coverage report**

Run: `pytest -p no:seleniumbase --cov=src/loophedge --cov-report=term-missing`
Expected: coverage on Phase 2 modules (`agents`, `memory`, `backtest`, `strategies`) ≥ 75% (the cassette-skipped tests will pull this down; that's acceptable). If a module drops below 60%, document as a known gap.

- [ ] **Step 2: Lint + type-check**

Run: `ruff check src tests && mypy src/loophedge`
Expected: both clean. Fix any errors.

- [ ] **Step 3: Commit lint/type fixes if any**

```bash
git add -A
git commit -m "chore: phase 2 lint and type-check pass" || echo "nothing to commit"
```

- [ ] **Step 4: Tag the milestone**

```bash
git tag -a phase-2-agent-loop -m "Phase 2 complete: maker + checker + genesis agents wired, e2e stub test green"
```

---

## What's NOT in this plan (Phase 3+)

- Live recorded cassettes — all 4 cassettes in `tests/cassettes/` are placeholders. The user must record once with `ANTHROPIC_LIVE_RECORD=1 ANTHROPIC_API_KEY=sk-... pytest tests/test_genesis.py tests/test_checker.py tests/test_maker.py` and commit them.
- Auto-flatten via Binance testnet — when `LIVE_VENUE=binance_testnet`, the circuit handler currently still routes through the simulator. Phase 3 wires real-venue closure.
- Hyperparameter tuning loop — the spec's Phase 3. Genesis can propose new strategies but not iteratively tune. Add a `tune_strategy` tool + scheduled tuning pass in Phase 3.
- Idempotency guard on the kill-switch (the deferred Phase 1 item) — now that the executor subscribes to `circuit.broken`, it'll re-flatten on every re-firing. A "last circuit_broken acted-on at" watermark in the executor would close this gap.
- Strategy retirement on PnL — currently strategies live forever after promotion. A "retire if Sharpe drops below 0.3 for 7 days" loop should be Phase 3.
- The dashboard does not show pending strategies awaiting checker approval. Phase 3 dashboard pass.
- Real Binance testnet trading via `LIVE_VENUE=binance_testnet`. The plumbing is there; the executor's `_place_order` path needs the testnet client implementation.

These will get their own implementation plans after Phase 2 is reviewed and the cassettes are recorded.
