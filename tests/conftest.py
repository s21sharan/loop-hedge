import os
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest
import vcr as _vcr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from loophedge.models import Base

# Workaround for vcrpy 6.0.2 + aiohttp incompatibility
try:
    import aiohttp.streams
    if not hasattr(aiohttp.streams, 'AsyncStreamReaderMixin'):
        # Create a dummy class if it doesn't exist
        class AsyncStreamReaderMixin:
            pass
        aiohttp.streams.AsyncStreamReaderMixin = AsyncStreamReaderMixin
except Exception:
    pass


@pytest.fixture
def starting_capital() -> Decimal:
    return Decimal("100000")


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, future=True, expire_on_commit=False)
    Base.metadata.drop_all(engine)
    engine.dispose()


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
            custom_patches=(),
        )
        with cfg.use_cassette(cassette_path):
            yield
    return _ctx
