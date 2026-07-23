import asyncio
import sys


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
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
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
    from loophedge.agents.client import AgentClient
    from loophedge.agents.genesis import GenesisAgent
    from loophedge.db import get_session_factory
    from loophedge.memory.lessons import LessonsLog
    from loophedge.memory.skills import SkillsRepo
    from loophedge.strategies.registry import StrategyRegistry
    from pathlib import Path as _Path

    async def _go():
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
