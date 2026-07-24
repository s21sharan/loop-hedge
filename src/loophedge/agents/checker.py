import json
from datetime import UTC, datetime

from loophedge.agents.client import AgentClient, ToolSpec
from loophedge.agents.tools import (
    make_read_skill, make_read_lessons, make_run_backtest,
)
from loophedge.bus import CH_SIGNAL_REJECTED, CH_SIGNAL_VERIFIED, Bus
from loophedge.memory.lessons import LessonsLog
from loophedge.memory.skills import SkillsRepo
from loophedge.schemas import SignalRejected, SignalVerified
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

    def validate_strategy(self, strategy_name: str) -> str:
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

    async def verify_signal(self, signal_id: str, strategy_name: str) -> str:
        from loophedge.models import Strategy
        with self.registry.session_factory() as s:
            row = s.query(Strategy).filter_by(name=strategy_name).one_or_none()
        if row is None:
            await self.bus.publish(CH_SIGNAL_REJECTED, SignalRejected(
                signal_id=signal_id, verdict="reject",
                reason=f"strategy {strategy_name} not found"))
            return "rejected"
        if row.status == "retired":
            await self.bus.publish(CH_SIGNAL_REJECTED, SignalRejected(
                signal_id=signal_id, verdict="reject", reason="strategy retired"))
            return "rejected"
        if row.status == "pending":
            verdict = self.validate_strategy(strategy_name)
            if verdict != "approved":
                await self.bus.publish(CH_SIGNAL_REJECTED, SignalRejected(
                    signal_id=signal_id, verdict="reject",
                    reason=f"strategy validation failed: {verdict}"))
                return "rejected"
        # Strategy is active. Publish verified.
        await self.bus.publish(CH_SIGNAL_VERIFIED, SignalVerified(
            signal_id=signal_id, verdict="approve",
            notes=f"strategy {strategy_name} active"))
        return "approved"


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
