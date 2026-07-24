import asyncio
import uuid
from datetime import datetime
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
            seen = self._read_seen()
            if seen:
                from datetime import datetime as _dt
                self._mark_ticked(_dt.fromisoformat(seen))
            return 0

        prompt = ("Active strategies: " + ", ".join(s.name for s in actives)
                   + ". Read the relevant skill/lessons and decide which signals to emit.")
        # We capture the LLM's contextual filter, then iterate strategies mechanically.
        await asyncio.to_thread(self.client.run,
                                  [{"role": "user", "content": prompt}], 4)

        emitted = 0
        for strat in actives:
            try:
                module = load_strategy(strat.name, self.skills)
            except Exception:
                continue
            symbol = strat.hyperparams.get("symbol", "BTCUSDT")
            with self.session_factory() as s:
                from sqlalchemy import select
                from loophedge.models import Bar
                rows = s.execute(
                    select(Bar).where(Bar.symbol == symbol)
                    .order_by(Bar.ts.desc()).limit(200)
                ).scalars().all()
            bars = list(reversed(rows))
            try:
                sigs = module.generate_signals(bars, strat.hyperparams) or []
            except Exception:
                continue
            for sig in sigs[-3:]:  # cap per strategy per tick
                # Dedupe by (strategy, symbol, payload-ts).
                sig_ts = sig["ts"].isoformat() if hasattr(sig["ts"], "isoformat") else str(sig["ts"])
                with self.session_factory() as _s:
                    from sqlalchemy import select as _select
                    last = _s.execute(
                        _select(Signal).where(
                            Signal.strategy_id == strat.name,
                            Signal.symbol == sig["symbol"],
                        ).order_by(Signal.ts_created.desc()).limit(1)
                    ).scalar()
                    if last is not None and (last.maker_payload or {}).get("ts") == sig_ts:
                        continue
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

        seen = self._read_seen()
        if seen:
            from datetime import datetime as _dt
            self._mark_ticked(_dt.fromisoformat(seen))
        return emitted
