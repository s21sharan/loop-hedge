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
    import asyncio as _asyncio
    import redis.asyncio
    from decimal import Decimal
    from loophedge.bus import CH_BAR_CLOSED, Bus
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
        latest_prices: dict = {}
        ex = Executor(bus, sf, sim, latest_prices=latest_prices,
                      venue=settings.live_venue)
        svc = ExecutorService(ex, bus, sim, sf)

        async def _track_prices():
            async for msg in bus.subscribe(CH_BAR_CLOSED):
                try:
                    latest_prices[msg["symbol"]] = Decimal(str(msg["close"]))
                except Exception:
                    pass

        price_task = _asyncio.create_task(_track_prices())
        try:
            await svc.run()
        finally:
            price_task.cancel()
            try:
                await price_task
            except _asyncio.CancelledError:
                pass
    asyncio.run(_go())


def run_risk() -> None:
    import asyncio as _asyncio
    import redis.asyncio
    from datetime import UTC, datetime
    from decimal import Decimal
    from loophedge.bus import Bus
    from loophedge.config import get_settings
    from loophedge.db import get_session_factory
    from loophedge.services.risk_monitor import RiskMonitor, compute_equity

    settings = get_settings()
    async def _go():
        redis_client = redis.asyncio.from_url(settings.redis_url)
        bus = Bus(redis_client)
        sf = get_session_factory()
        rm = RiskMonitor(bus, sf, kill_dd_pct=Decimal(str(settings.kill_switch_dd_pct)))
        starting = Decimal(str(settings.starting_capital_usd))
        while True:
            with sf() as s:
                equity = compute_equity(s, starting)
            await rm.tick(datetime.now(UTC), equity)
            await _asyncio.sleep(60)
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
            if not maker.should_tick():
                return
            try:
                n = await maker.tick()
                print(f"[maker] tick emitted {n} signal(s)", flush=True)
            except Exception as e:
                print(f"[maker] tick failed: {e}", file=sys.stderr, flush=True)

        sched = AsyncIOScheduler()
        sched.add_job(_on_timer, "interval", minutes=15)
        sched.start()
        async for msg in bus.subscribe(CH_BAR_CLOSED):
            await _on_bar(msg)

    asyncio.run(_go())


def run_checker() -> None:
    import redis.asyncio
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
    from loophedge.agents.checker import CheckerAgent
    from loophedge.agents.client import AgentClient
    from loophedge.bus import CH_SIGNAL_CANDIDATE, Bus
    from loophedge.config import get_settings
    from loophedge.db import get_session_factory
    from loophedge.memory.lessons import LessonsLog
    from loophedge.memory.skills import SkillsRepo
    from loophedge.strategies.registry import StrategyRegistry
    from pathlib import Path as _Path

    import hashlib
    import json

    settings = get_settings()
    state_path = _Path("/app/state/checker_sweep_state.json")
    MAX_FAILED_ATTEMPTS = 3

    def _load_state() -> dict:
        try:
            return json.loads(state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_state(state: dict) -> None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state))

    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    async def _go():
        redis_client = redis.asyncio.from_url(settings.redis_url)
        bus = Bus(redis_client)
        sr = SkillsRepo(_Path("/app/skills"))
        lessons = LessonsLog(sr)
        reg = StrategyRegistry(get_session_factory(), sr)
        client = AgentClient(model="claude-opus-4-7", system_prompt="", tools=[])
        ck = CheckerAgent(client, reg, sr, lessons, get_session_factory(), bus)

        async def _sweep_pending() -> None:
            pending = reg.list_pending()
            if not pending:
                return
            state = _load_state()
            checked = 0
            skipped = 0
            for row in pending:
                try:
                    source = sr.read_strategy(row.name)
                except FileNotFoundError:
                    continue
                h = _hash(source)
                entry = state.get(row.name, {"attempts": 0, "hash": None})
                if entry["hash"] == h and entry["attempts"] > 0:
                    skipped += 1
                    continue
                checked += 1
                try:
                    verdict = await asyncio.to_thread(ck.validate_strategy, row.name)
                except Exception as e:
                    print(f"[checker] validate_strategy({row.name}) failed: {e}",
                          file=sys.stderr, flush=True)
                    continue
                if verdict == "needs_revision":
                    entry["attempts"] += 1
                    entry["hash"] = h
                    state[row.name] = entry
                    print(f"[checker] {row.name} -> needs_revision "
                          f"(attempt {entry['attempts']}/{MAX_FAILED_ATTEMPTS})",
                          flush=True)
                    if entry["attempts"] >= MAX_FAILED_ATTEMPTS:
                        try:
                            reg.retire(row.name, actor="checker",
                                       reason=f"failed validation {MAX_FAILED_ATTEMPTS}x")
                            state.pop(row.name, None)
                            print(f"[checker] retired {row.name} after "
                                  f"{MAX_FAILED_ATTEMPTS} failed attempts", flush=True)
                        except Exception as e:
                            print(f"[checker] retire({row.name}) failed: {e}",
                                  file=sys.stderr, flush=True)
                else:
                    state.pop(row.name, None)
                    print(f"[checker] {row.name} -> {verdict}", flush=True)
                _save_state(state)
            print(f"[checker] sweep done: {checked} checked, {skipped} skipped (unchanged)",
                  flush=True)

        # Run one sweep immediately, then hourly.
        await _sweep_pending()
        sched = AsyncIOScheduler()
        sched.add_job(_sweep_pending, "interval", minutes=30)
        sched.start()

        async for msg in bus.subscribe(CH_SIGNAL_CANDIDATE):
            signal_id = msg.get("signal_id")
            strategy_name = msg.get("strategy_id", "")
            if not signal_id or not strategy_name:
                continue
            try:
                await ck.verify_signal(signal_id, strategy_name)
            except Exception as e:
                print(f"[checker] verify_signal failed: {e}", file=sys.stderr, flush=True)

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
            try:
                name = await asyncio.to_thread(agent.propose_once)
                print(f"[genesis] proposed: {name or '(none)'}", flush=True)
            except Exception as e:
                print(f"[genesis] propose_once failed: {e}", file=sys.stderr, flush=True)
            await asyncio.sleep(14400)  # 4 hours — was hourly; cost-motivated
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
