"""Tool functions registered with AgentClient.

These wrap the agents' allowed side effects. Each function returns a
JSON-serializable dict. Keep functions small and obviously safe — they
are the chokepoint that limits what the LLM can do.
"""
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
