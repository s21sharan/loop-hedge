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
