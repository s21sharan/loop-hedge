import asyncio
import sys


def run_ingest() -> None:
    import redis.asyncio
    from loophedge.bus import Bus
    from loophedge.config import get_settings
    from loophedge.db import SessionLocal
    from loophedge.services.data_ingestor import (
        DataIngestor, binance_fetch_klines,
    )

    settings = get_settings()
    async def _go():
        redis_client = redis.asyncio.from_url(settings.redis_url)
        bus = Bus(redis_client)
        ing = DataIngestor(bus, SessionLocal, binance_fetch_klines,
                            settings.symbols, settings.bar_timeframe)
        while True:
            await ing.fetch_and_publish_once()
            await asyncio.sleep(60)
    asyncio.run(_go())


def run_execute() -> None:
    # Phase 2 will wire this; Phase 0-1 leaves a placeholder runner
    # that does not auto-start (executor is used via library calls in tests).
    raise SystemExit("execute service requires Phase 2 wiring")


def run_risk() -> None:
    raise SystemExit("risk service requires Phase 2 wiring")


def run_dashboard() -> None:
    import uvicorn
    from loophedge.db import SessionLocal
    from loophedge.services.dashboard import build_app
    app = build_app(SessionLocal)
    uvicorn.run(app, host="0.0.0.0", port=8000)


_COMMANDS = ("ingest", "execute", "risk", "dashboard")


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] not in _COMMANDS:
        print(f"usage: python -m loophedge {{{'|'.join(_COMMANDS)}}}", file=sys.stderr)
        return 2
    # Resolve via module globals so tests can monkeypatch run_* in this module.
    globals()[f"run_{argv[0]}"]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
