# Phase 0 + Phase 1: Scaffolding + Deterministic Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic foundation of the loop-engineered mock hedge fund: repo scaffolding, Postgres + Redis, a self-sim fill ledger, a data-ingestor pulling Binance bars, an executor that consumes hardcoded signals through risk caps, a risk monitor that enforces a hard drawdown kill switch, and a read-only dashboard. **No LLM yet.** The end-to-end test of this phase is: a hardcoded buy/sell sequence flows through the pipeline and produces a correct PnL curve.

**Architecture:** Eight-container Docker Compose system. This plan implements the four no-LLM containers (`data-ingestor`, `executor`, `risk-monitor`, `dashboard`) plus the `postgres` and `redis` infrastructure. The LLM containers (`maker-agent`, `checker-agent`, `strategy-genesis-agent`) are stubbed as empty Dockerfiles for Phase 2. Services communicate via Redis pub/sub. State lives in Postgres + a `state/` volume.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x + Alembic, psycopg 3 (sync core, async for services), redis-py (async), python-binance, pydantic v2, pytest + pytest-asyncio, Docker Compose.

## Global Constraints

- Python 3.12. All services use the same runtime image.
- All money is mock. Default starting capital: $100,000 USD.
- Position size hard cap: 5% of equity per trade. Enforced in code, not config.
- Per-strategy hard cap: 25% of equity across all open positions for any one strategy.
- Portfolio kill switch: 15% drawdown from rolling 30-day equity high.
- All amounts stored in Postgres as `NUMERIC(20, 8)` to avoid float precision issues.
- Time is UTC everywhere. All timestamps are `TIMESTAMPTZ`.
- Tests use deterministic fixtures. No live network in unit tests.
- Commit messages: conventional commits format (`feat:`, `fix:`, `test:`, `chore:`). Do NOT add Claude as a co-author.
- The implementer must NOT run `docker compose up` or start any backend server during plan execution — that is the user's call. Plan execution stops at "all tests pass + images build."
- No npm / node — this is a Python project.

## File Structure

```
loop-hedge/
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── .gitignore
├── README.md
├── alembic.ini
├── migrations/
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py
├── src/loophedge/
│   ├── __init__.py
│   ├── config.py                   # env vars via pydantic-settings
│   ├── db.py                       # SQLAlchemy engine + session
│   ├── bus.py                      # redis pub/sub helpers
│   ├── models.py                   # SQLAlchemy ORM models
│   ├── schemas.py                  # pydantic event/payload schemas
│   ├── risk/
│   │   ├── __init__.py
│   │   └── caps.py                 # hard caps (constants + enforcer)
│   ├── ledger/
│   │   ├── __init__.py
│   │   └── simulator.py            # self-sim fill engine
│   ├── services/
│   │   ├── __init__.py
│   │   ├── data_ingestor.py        # Binance puller → bars + bar.closed events
│   │   ├── executor.py             # signal.verified → fills
│   │   ├── risk_monitor.py         # drawdown + kill switch
│   │   └── dashboard.py            # FastAPI read-only UI
│   └── cli.py                      # `python -m loophedge <service>` entry
├── tests/
│   ├── conftest.py                 # shared fixtures, in-memory db
│   ├── test_risk_caps.py
│   ├── test_simulator.py
│   ├── test_data_ingestor.py
│   ├── test_executor.py
│   ├── test_risk_monitor.py
│   ├── test_dashboard.py
│   └── test_e2e_replay.py
└── docker/
    ├── base.Dockerfile             # one shared image, service via CMD
    └── README.md
```

Rationale: each service file does one job and is independently testable. Shared infrastructure (`config`, `db`, `bus`, `models`, `schemas`) lives at the top level so service files stay small. Strategies, ledger, and risk are their own modules because each has clear boundaries and will grow in Phase 2+.

---

### Task 1: Project bootstrap

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`
- Create: `src/loophedge/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a Python package `loophedge` importable from tests; pytest collects tests from `tests/`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "loophedge"
version = "0.1.0"
description = "Loop-engineered mock hedge fund"
requires-python = ">=3.12"
dependencies = [
  "fastapi==0.115.*",
  "uvicorn[standard]==0.32.*",
  "pydantic==2.9.*",
  "pydantic-settings==2.6.*",
  "sqlalchemy==2.0.*",
  "alembic==1.13.*",
  "psycopg[binary,pool]==3.2.*",
  "redis==5.2.*",
  "python-binance==1.0.*",
  "httpx==0.27.*",
  "jinja2==3.1.*",
  "apscheduler==3.10.*",
]

[project.optional-dependencies]
dev = [
  "pytest==8.3.*",
  "pytest-asyncio==0.24.*",
  "pytest-cov==5.0.*",
  "fakeredis==2.26.*",
  "freezegun==1.5.*",
  "ruff==0.7.*",
  "mypy==1.13.*",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-ra --strict-markers"

[tool.ruff]
line-length = 100
target-version = "py312"
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.venv/
.env
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/
htmlcov/
.coverage
dist/
build/
state/
```

- [ ] **Step 3: Write `.env.example`**

```bash
# Database
DATABASE_URL=postgresql+psycopg://loophedge:loophedge@postgres:5432/loophedge

# Redis
REDIS_URL=redis://redis:6379/0

# Trading
LIVE_VENUE=simulator              # simulator | binance_testnet
STARTING_CAPITAL_USD=100000
SYMBOLS=BTCUSDT,ETHUSDT
BAR_TIMEFRAME=5m

# Risk
MAX_POSITION_PCT=0.05
MAX_STRATEGY_ALLOC_PCT=0.25
KILL_SWITCH_DD_PCT=0.15

# Binance testnet (only when LIVE_VENUE=binance_testnet)
BINANCE_API_KEY=
BINANCE_API_SECRET=
```

- [ ] **Step 4: Write `README.md`**

```markdown
# loop-hedge

A loop-engineered mock hedge fund. See `docs/superpowers/specs/2026-06-29-loop-hedge-design.md`.

## Quick start (dev)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Phases

- Phase 0–1: deterministic core (this branch). No LLM.
- Phase 2+: agent layer.
```

- [ ] **Step 5: Write empty package + test bootstrap**

`src/loophedge/__init__.py`:
```python
__version__ = "0.1.0"
```

`tests/__init__.py`:
```python
```

`tests/conftest.py`:
```python
import pytest


@pytest.fixture
def starting_capital() -> float:
    return 100_000.0
```

- [ ] **Step 6: Verify package installs and pytest runs**

Run: `pip install -e ".[dev]" && pytest`
Expected: pytest collects zero tests, exits 0 with "no tests ran".

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore .env.example README.md src/ tests/
git commit -m "chore: bootstrap python package and pytest"
```

---

### Task 2: Config module

**Files:**
- Create: `src/loophedge/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings` class exposing `database_url: str`, `redis_url: str`, `live_venue: Literal["simulator", "binance_testnet"]`, `starting_capital_usd: float`, `symbols: list[str]`, `bar_timeframe: str`, `max_position_pct: float`, `max_strategy_alloc_pct: float`, `kill_switch_dd_pct: float`, `binance_api_key: str | None`, `binance_api_secret: str | None`. Factory: `get_settings() -> Settings` (cached).

- [ ] **Step 1: Write failing test**

`tests/test_config.py`:
```python
import os
from loophedge.config import Settings


def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x")
    monkeypatch.setenv("REDIS_URL", "redis://r")
    s = Settings()
    assert s.starting_capital_usd == 100_000.0
    assert s.max_position_pct == 0.05
    assert s.symbols == ["BTCUSDT", "ETHUSDT"]


def test_symbols_parses_csv(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "x")
    monkeypatch.setenv("REDIS_URL", "x")
    monkeypatch.setenv("SYMBOLS", "BTCUSDT,SOLUSDT,DOGEUSDT")
    s = Settings()
    assert s.symbols == ["BTCUSDT", "SOLUSDT", "DOGEUSDT"]


def test_live_venue_rejects_invalid(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "x")
    monkeypatch.setenv("REDIS_URL", "x")
    monkeypatch.setenv("LIVE_VENUE", "kraken")
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings()
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_config.py -v`
Expected: ImportError on `loophedge.config`.

- [ ] **Step 3: Implement `src/loophedge/config.py`**

```python
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str

    live_venue: Literal["simulator", "binance_testnet"] = "simulator"
    starting_capital_usd: float = 100_000.0
    symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    bar_timeframe: str = "5m"

    max_position_pct: float = 0.05
    max_strategy_alloc_pct: float = 0.25
    kill_switch_dd_pct: float = 0.15

    binance_api_key: str | None = None
    binance_api_secret: str | None = None

    @field_validator("symbols", mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_config.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/loophedge/config.py tests/test_config.py
git commit -m "feat: add typed settings with env loading"
```

---

### Task 3: Database models + Alembic migration

**Files:**
- Create: `src/loophedge/models.py`
- Create: `src/loophedge/db.py`
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/001_initial_schema.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Produces: SQLAlchemy `Base` + ORM models `Bar`, `Signal`, `Fill`, `Position`, `EquitySnapshot`, `Strategy`, `Backtest`, `RiskEvent` (columns per spec §6.1). `db.engine` (sync) + `db.SessionLocal` factory.

- [ ] **Step 1: Write failing test**

`tests/test_models.py`:
```python
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from loophedge.models import Base, Bar, Fill, Position, Signal


def _engine():
    e = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(e)
    return e


def test_signal_lifecycle_columns():
    e = _engine()
    with Session(e) as s:
        sig = Signal(
            strategy_id="momentum_btc",
            symbol="BTCUSDT",
            side="long",
            size_pct=Decimal("0.02"),
            status="candidate",
            maker_payload={"reason": "rsi cross"},
        )
        s.add(sig)
        s.commit()
        loaded = s.query(Signal).one()
        assert loaded.status == "candidate"
        assert loaded.maker_payload["reason"] == "rsi cross"


def test_bar_primary_key_is_composite():
    e = _engine()
    with Session(e) as s:
        s.add(Bar(symbol="BTCUSDT", timeframe="5m", ts=datetime.now(UTC),
                  open=Decimal("1"), high=Decimal("1"), low=Decimal("1"),
                  close=Decimal("1"), volume=Decimal("1")))
        s.commit()


def test_position_unique_by_symbol():
    e = _engine()
    with Session(e) as s:
        s.add(Position(symbol="BTCUSDT", qty=Decimal("0.1"),
                       avg_entry=Decimal("60000"),
                       unrealized_pnl=Decimal("0"),
                       updated_at=datetime.now(UTC)))
        s.commit()
        assert s.query(Position).count() == 1
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_models.py -v`
Expected: ImportError on `loophedge.models`.

- [ ] **Step 3: Implement `src/loophedge/models.py`**

```python
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


M20_8 = Numeric(20, 8)


class Bar(Base):
    __tablename__ = "bars"
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[Decimal] = mapped_column(M20_8)
    high: Mapped[Decimal] = mapped_column(M20_8)
    low: Mapped[Decimal] = mapped_column(M20_8)
    close: Mapped[Decimal] = mapped_column(M20_8)
    volume: Mapped[Decimal] = mapped_column(M20_8)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    ts_created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now())
    strategy_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(8))     # long | short | flat
    size_pct: Mapped[Decimal] = mapped_column(M20_8)
    status: Mapped[str] = mapped_column(String(16))  # candidate|approved|rejected|executed|killed
    maker_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    checker_verdict: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)


class Fill(Base):
    __tablename__ = "fills"
    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    symbol: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[Decimal] = mapped_column(M20_8)
    price: Mapped[Decimal] = mapped_column(M20_8)
    fees: Mapped[Decimal] = mapped_column(M20_8)
    venue: Mapped[str] = mapped_column(String(32))   # simulator | binance_testnet


class Position(Base):
    __tablename__ = "positions"
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    qty: Mapped[Decimal] = mapped_column(M20_8)
    avg_entry: Mapped[Decimal] = mapped_column(M20_8)
    unrealized_pnl: Mapped[Decimal] = mapped_column(M20_8)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    cash: Mapped[Decimal] = mapped_column(M20_8)
    equity: Mapped[Decimal] = mapped_column(M20_8)
    drawdown_pct: Mapped[Decimal] = mapped_column(M20_8)


class Strategy(Base):
    __tablename__ = "strategies"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))   # active | pending | retired
    source_path: Mapped[str] = mapped_column(String(256))
    hyperparams: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promoted_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    retired_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)


class Backtest(Base):
    __tablename__ = "backtests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    strategy_id: Mapped[str] = mapped_column(String(64))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sharpe: Mapped[Decimal] = mapped_column(M20_8)
    max_dd_pct: Mapped[Decimal] = mapped_column(M20_8)
    t_stat: Mapped[Decimal] = mapped_column(M20_8)
    trade_count: Mapped[int] = mapped_column(Integer)
    passed: Mapped[bool] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(String(2048), nullable=True)


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    actions_taken: Mapped[dict] = mapped_column(JSON, default=dict)
```

- [ ] **Step 4: Implement `src/loophedge/db.py`**

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from loophedge.config import get_settings

_settings = get_settings()
engine = create_engine(_settings.database_url, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest tests/test_models.py -v`
Expected: 3 passed.

- [ ] **Step 6: Scaffold Alembic**

`alembic.ini` (only `script_location` shown; the rest is the standard alembic template):
```ini
[alembic]
script_location = migrations
sqlalchemy.url =

[loggers]
keys = root
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = INFO
handlers = console
[handler_console]
class = StreamHandler
args = (sys.stderr,)
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

`migrations/env.py`:
```python
from alembic import context
from sqlalchemy import engine_from_config, pool

from loophedge.config import get_settings
from loophedge.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"),
                      target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section),
                                     prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`migrations/versions/001_initial_schema.py`:
```python
"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-06-29
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None


def upgrade():
    from loophedge.models import Base
    Base.metadata.create_all(op.get_bind())


def downgrade():
    from loophedge.models import Base
    Base.metadata.drop_all(op.get_bind())
```

- [ ] **Step 7: Commit**

```bash
git add src/loophedge/models.py src/loophedge/db.py alembic.ini migrations/ tests/test_models.py
git commit -m "feat: postgres schema, sqlalchemy models, alembic migration"
```

---

### Task 4: Event schemas + Redis bus

**Files:**
- Create: `src/loophedge/schemas.py`
- Create: `src/loophedge/bus.py`
- Create: `tests/test_bus.py`

**Interfaces:**
- Produces (schemas): pydantic models `BarClosed`, `SignalCandidate`, `SignalVerified`, `SignalRejected`, `CircuitBroken`. Each has `model_dump_json()` and `model_validate_json()`.
- Produces (bus): `Bus` class with `await bus.publish(channel: str, payload: BaseModel)` and `async def bus.subscribe(channel: str) -> AsyncIterator[dict]`. Uses `redis.asyncio.Redis`. Constructor takes a `Redis` instance for testability (so tests inject `fakeredis.aioredis.FakeRedis`).
- Channels (constants): `CH_BAR_CLOSED = "bar.closed"`, `CH_SIGNAL_CANDIDATE = "signal.candidate"`, `CH_SIGNAL_VERIFIED = "signal.verified"`, `CH_SIGNAL_REJECTED = "signal.rejected"`, `CH_CIRCUIT_BROKEN = "circuit.broken"`.

- [ ] **Step 1: Write failing test**

`tests/test_bus.py`:
```python
import asyncio

import fakeredis.aioredis
import pytest

from loophedge.bus import Bus, CH_BAR_CLOSED
from loophedge.schemas import BarClosed


@pytest.mark.asyncio
async def test_publish_subscribe_roundtrip():
    redis = fakeredis.aioredis.FakeRedis()
    bus = Bus(redis)

    received = []

    async def consumer():
        async for msg in bus.subscribe(CH_BAR_CLOSED):
            received.append(msg)
            break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)  # let subscriber attach

    bar = BarClosed(symbol="BTCUSDT", timeframe="5m",
                    ts="2026-06-29T12:00:00+00:00",
                    open="60000", high="60100", low="59900",
                    close="60050", volume="12.5")
    await bus.publish(CH_BAR_CLOSED, bar)

    await asyncio.wait_for(task, timeout=1.0)
    assert received[0]["symbol"] == "BTCUSDT"
    assert received[0]["close"] == "60050"
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_bus.py -v`
Expected: ImportError on `loophedge.bus`.

- [ ] **Step 3: Implement `src/loophedge/schemas.py`**

```python
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BarClosed(_Strict):
    symbol: str
    timeframe: str
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class SignalCandidate(_Strict):
    signal_id: str
    strategy_id: str
    symbol: str
    side: Literal["long", "short", "flat"]
    size_pct: Decimal
    reasoning: str


class SignalVerified(_Strict):
    signal_id: str
    verdict: Literal["approve"]
    notes: str | None = None


class SignalRejected(_Strict):
    signal_id: str
    verdict: Literal["reject", "needs_revision"]
    reason: str


class CircuitBroken(_Strict):
    ts: datetime
    drawdown_pct: Decimal
    action: Literal["flatten_all", "pause_makers"]
```

- [ ] **Step 4: Implement `src/loophedge/bus.py`**

```python
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel
from redis.asyncio import Redis

CH_BAR_CLOSED = "bar.closed"
CH_SIGNAL_CANDIDATE = "signal.candidate"
CH_SIGNAL_VERIFIED = "signal.verified"
CH_SIGNAL_REJECTED = "signal.rejected"
CH_CIRCUIT_BROKEN = "circuit.broken"


class Bus:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def publish(self, channel: str, payload: BaseModel) -> None:
        await self.redis.publish(channel, payload.model_dump_json())

    async def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]:
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                import json
                yield json.loads(msg["data"])
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest tests/test_bus.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add src/loophedge/schemas.py src/loophedge/bus.py tests/test_bus.py
git commit -m "feat: pydantic event schemas and redis bus with channel constants"
```

---

### Task 5: Risk caps module

**Files:**
- Create: `src/loophedge/risk/__init__.py`
- Create: `src/loophedge/risk/caps.py`
- Create: `tests/test_risk_caps.py`

**Interfaces:**
- Produces: constants `HARD_MAX_POSITION_PCT = 0.05`, `HARD_MAX_STRATEGY_ALLOC_PCT = 0.25`, `HARD_KILL_SWITCH_DD_PCT = 0.15`. Function `enforce_pretrade(equity: Decimal, current_positions: dict[str, Decimal], strategy_allocations: dict[str, Decimal], proposed: ProposedTrade) -> CapVerdict`. `ProposedTrade` is a dataclass (strategy_id, symbol, side, size_pct). `CapVerdict` is a dataclass (`allowed: bool`, `reason: str | None`).

- [ ] **Step 1: Write failing tests**

`tests/test_risk_caps.py`:
```python
from decimal import Decimal

import pytest

from loophedge.risk.caps import (
    HARD_KILL_SWITCH_DD_PCT,
    HARD_MAX_POSITION_PCT,
    HARD_MAX_STRATEGY_ALLOC_PCT,
    CapVerdict,
    ProposedTrade,
    enforce_pretrade,
)


def _pt(size_pct: str, strat: str = "s1", symbol: str = "BTCUSDT", side: str = "long"):
    return ProposedTrade(strategy_id=strat, symbol=symbol, side=side, size_pct=Decimal(size_pct))


def test_constants_match_spec():
    assert HARD_MAX_POSITION_PCT == Decimal("0.05")
    assert HARD_MAX_STRATEGY_ALLOC_PCT == Decimal("0.25")
    assert HARD_KILL_SWITCH_DD_PCT == Decimal("0.15")


def test_allows_within_caps():
    v = enforce_pretrade(equity=Decimal("100000"),
                         current_positions={},
                         strategy_allocations={},
                         proposed=_pt("0.02"))
    assert v.allowed


def test_rejects_oversized_position():
    v = enforce_pretrade(equity=Decimal("100000"),
                         current_positions={},
                         strategy_allocations={},
                         proposed=_pt("0.06"))
    assert not v.allowed
    assert "position size" in v.reason.lower()


def test_rejects_strategy_alloc_breach():
    v = enforce_pretrade(equity=Decimal("100000"),
                         current_positions={},
                         strategy_allocations={"s1": Decimal("0.24")},
                         proposed=_pt("0.02"))
    assert not v.allowed
    assert "strategy" in v.reason.lower()


def test_negative_size_rejected():
    v = enforce_pretrade(equity=Decimal("100000"),
                         current_positions={},
                         strategy_allocations={},
                         proposed=_pt("-0.01"))
    assert not v.allowed
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_risk_caps.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `src/loophedge/risk/__init__.py`** (empty file)

- [ ] **Step 4: Implement `src/loophedge/risk/caps.py`**

```python
from dataclasses import dataclass
from decimal import Decimal

HARD_MAX_POSITION_PCT = Decimal("0.05")
HARD_MAX_STRATEGY_ALLOC_PCT = Decimal("0.25")
HARD_KILL_SWITCH_DD_PCT = Decimal("0.15")


@dataclass(frozen=True)
class ProposedTrade:
    strategy_id: str
    symbol: str
    side: str
    size_pct: Decimal


@dataclass(frozen=True)
class CapVerdict:
    allowed: bool
    reason: str | None = None


def enforce_pretrade(
    equity: Decimal,
    current_positions: dict[str, Decimal],
    strategy_allocations: dict[str, Decimal],
    proposed: ProposedTrade,
) -> CapVerdict:
    if proposed.size_pct <= 0:
        return CapVerdict(False, "non-positive size_pct rejected")
    if proposed.size_pct > HARD_MAX_POSITION_PCT:
        return CapVerdict(False, f"position size {proposed.size_pct} exceeds hard cap {HARD_MAX_POSITION_PCT}")
    current = strategy_allocations.get(proposed.strategy_id, Decimal("0"))
    if current + proposed.size_pct > HARD_MAX_STRATEGY_ALLOC_PCT:
        return CapVerdict(False, f"strategy {proposed.strategy_id} would exceed alloc cap {HARD_MAX_STRATEGY_ALLOC_PCT}")
    return CapVerdict(True)
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest tests/test_risk_caps.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/loophedge/risk/ tests/test_risk_caps.py
git commit -m "feat: hard risk caps with pre-trade enforcement"
```

---

### Task 6: Self-sim fill ledger

**Files:**
- Create: `src/loophedge/ledger/__init__.py`
- Create: `src/loophedge/ledger/simulator.py`
- Create: `tests/test_simulator.py`

**Interfaces:**
- Produces: `Simulator` class with `apply_fill(symbol: str, side: str, qty: Decimal, ref_price: Decimal, ts: datetime, fee_bps: Decimal = Decimal("10")) -> Fill`. Internal state: cash, positions dict. Public properties: `cash`, `positions`, `equity(mark_prices: dict[str, Decimal]) -> Decimal`. Slippage model: fill price = ref_price * (1 ± 5bps) depending on side. `Fill` is a dataclass mirroring the ORM model.

- [ ] **Step 1: Write failing tests**

`tests/test_simulator.py`:
```python
from datetime import UTC, datetime
from decimal import Decimal

from loophedge.ledger.simulator import Fill, Simulator


def test_buy_reduces_cash_creates_position():
    sim = Simulator(starting_cash=Decimal("100000"))
    fill = sim.apply_fill(symbol="BTCUSDT", side="long", qty=Decimal("0.1"),
                           ref_price=Decimal("60000"), ts=datetime.now(UTC))
    assert isinstance(fill, Fill)
    assert fill.price > Decimal("60000")  # buy slips up
    assert sim.cash < Decimal("100000")
    assert sim.positions["BTCUSDT"].qty == Decimal("0.1")


def test_sell_increases_cash_closes_position():
    sim = Simulator(starting_cash=Decimal("100000"))
    sim.apply_fill("BTCUSDT", "long", Decimal("0.1"), Decimal("60000"), datetime.now(UTC))
    sim.apply_fill("BTCUSDT", "short", Decimal("0.1"), Decimal("65000"), datetime.now(UTC))
    assert sim.positions["BTCUSDT"].qty == Decimal("0")
    assert sim.cash > Decimal("100000")


def test_fees_deducted_from_cash():
    sim = Simulator(starting_cash=Decimal("100000"))
    fill = sim.apply_fill("BTCUSDT", "long", Decimal("1"), Decimal("60000"),
                          datetime.now(UTC), fee_bps=Decimal("10"))
    # fee_bps=10 → 0.1% of notional = 60
    assert fill.fees == Decimal("60.00000000")


def test_equity_marks_to_market():
    sim = Simulator(starting_cash=Decimal("100000"))
    sim.apply_fill("BTCUSDT", "long", Decimal("0.1"), Decimal("60000"), datetime.now(UTC))
    e = sim.equity({"BTCUSDT": Decimal("65000")})
    assert e > sim.cash


def test_short_sells_uncovered():
    sim = Simulator(starting_cash=Decimal("100000"))
    sim.apply_fill("BTCUSDT", "short", Decimal("0.1"), Decimal("60000"), datetime.now(UTC))
    assert sim.positions["BTCUSDT"].qty == Decimal("-0.1")
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_simulator.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `src/loophedge/ledger/__init__.py`** (empty file)

- [ ] **Step 4: Implement `src/loophedge/ledger/simulator.py`**

```python
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

SLIPPAGE_BPS = Decimal("5")
BPS = Decimal("10000")


@dataclass
class Fill:
    id: str
    ts: datetime
    symbol: str
    side: str
    qty: Decimal
    price: Decimal
    fees: Decimal
    venue: str = "simulator"


@dataclass
class _Position:
    symbol: str
    qty: Decimal = Decimal("0")
    avg_entry: Decimal = Decimal("0")


@dataclass
class Simulator:
    starting_cash: Decimal
    cash: Decimal = field(init=False)
    positions: dict[str, _Position] = field(default_factory=dict)

    def __post_init__(self):
        self.cash = self.starting_cash

    def apply_fill(
        self,
        symbol: str,
        side: str,
        qty: Decimal,
        ref_price: Decimal,
        ts: datetime,
        fee_bps: Decimal = Decimal("10"),
    ) -> Fill:
        slip = ref_price * SLIPPAGE_BPS / BPS
        fill_price = ref_price + slip if side == "long" else ref_price - slip
        notional = fill_price * qty
        fees = (notional * fee_bps / BPS).quantize(Decimal("0.00000001"))

        signed_qty = qty if side == "long" else -qty
        pos = self.positions.setdefault(symbol, _Position(symbol))
        # weighted avg entry, treating opposite-side fills as reductions
        new_qty = pos.qty + signed_qty
        if pos.qty == 0 or (pos.qty > 0) == (signed_qty > 0):
            total_cost = pos.avg_entry * abs(pos.qty) + fill_price * abs(signed_qty)
            pos.avg_entry = total_cost / abs(new_qty) if new_qty != 0 else Decimal("0")
        pos.qty = new_qty

        self.cash -= signed_qty * fill_price + fees
        return Fill(id=str(uuid.uuid4()), ts=ts, symbol=symbol, side=side,
                    qty=qty, price=fill_price, fees=fees)

    def equity(self, mark_prices: dict[str, Decimal]) -> Decimal:
        unrealized = sum(
            (mark_prices.get(p.symbol, p.avg_entry) - p.avg_entry) * p.qty
            for p in self.positions.values()
        )
        return self.cash + Decimal(unrealized)
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest tests/test_simulator.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/loophedge/ledger/ tests/test_simulator.py
git commit -m "feat: self-sim fill ledger with slippage and fees"
```

---

### Task 7: Data ingestor service

**Files:**
- Create: `src/loophedge/services/__init__.py`
- Create: `src/loophedge/services/data_ingestor.py`
- Create: `tests/test_data_ingestor.py`

**Interfaces:**
- Produces: `class DataIngestor` with constructor `(bus: Bus, session_factory, fetch_klines: Callable[[str, str, int], Awaitable[list[dict]]], symbols: list[str], timeframe: str)`. Method `async fetch_and_publish_once() -> int` (returns count of new bars). The `fetch_klines` callable is injected so tests can pass a fake (no live network in tests).
- Each bar is upserted into Postgres `bars`. On insert (not update), publish `BarClosed` to `CH_BAR_CLOSED`.

- [ ] **Step 1: Write failing tests**

`tests/test_data_ingestor.py`:
```python
import asyncio
from decimal import Decimal

import fakeredis.aioredis
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from loophedge.bus import CH_BAR_CLOSED, Bus
from loophedge.models import Base, Bar
from loophedge.services.data_ingestor import DataIngestor


def _session_factory():
    e = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(e)
    return sessionmaker(bind=e, future=True), e


def _fake_klines_factory(rows):
    async def _f(symbol, timeframe, limit):
        return rows
    return _f


@pytest.mark.asyncio
async def test_persists_new_bars_and_publishes():
    sf, _ = _session_factory()
    redis = fakeredis.aioredis.FakeRedis()
    bus = Bus(redis)

    rows = [
        {"open_time": 1_700_000_000_000, "open": "60000", "high": "60100",
         "low": "59900", "close": "60050", "volume": "1.5"},
    ]
    ing = DataIngestor(bus, sf, _fake_klines_factory(rows),
                       symbols=["BTCUSDT"], timeframe="5m")

    received = []

    async def consumer():
        async for msg in bus.subscribe(CH_BAR_CLOSED):
            received.append(msg)
            break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)

    count = await ing.fetch_and_publish_once()
    assert count == 1

    await asyncio.wait_for(task, timeout=1.0)
    assert received[0]["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_dedupes_existing_bars():
    sf, _ = _session_factory()
    redis = fakeredis.aioredis.FakeRedis()
    bus = Bus(redis)
    rows = [
        {"open_time": 1_700_000_000_000, "open": "1", "high": "1",
         "low": "1", "close": "1", "volume": "1"},
    ]
    ing = DataIngestor(bus, sf, _fake_klines_factory(rows),
                       symbols=["BTCUSDT"], timeframe="5m")
    assert await ing.fetch_and_publish_once() == 1
    assert await ing.fetch_and_publish_once() == 0
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_data_ingestor.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `src/loophedge/services/__init__.py`** (empty file)

- [ ] **Step 4: Implement `src/loophedge/services/data_ingestor.py`**

```python
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from loophedge.bus import CH_BAR_CLOSED, Bus
from loophedge.models import Bar
from loophedge.schemas import BarClosed

FetchKlines = Callable[[str, str, int], Awaitable[list[dict]]]


class DataIngestor:
    def __init__(
        self,
        bus: Bus,
        session_factory: sessionmaker,
        fetch_klines: FetchKlines,
        symbols: list[str],
        timeframe: str,
        limit: int = 100,
    ):
        self.bus = bus
        self.session_factory = session_factory
        self.fetch_klines = fetch_klines
        self.symbols = symbols
        self.timeframe = timeframe
        self.limit = limit

    async def fetch_and_publish_once(self) -> int:
        inserted = 0
        for symbol in self.symbols:
            rows = await self.fetch_klines(symbol, self.timeframe, self.limit)
            for row in rows:
                ts = datetime.fromtimestamp(row["open_time"] / 1000, tz=UTC)
                bar = Bar(
                    symbol=symbol, timeframe=self.timeframe, ts=ts,
                    open=Decimal(row["open"]), high=Decimal(row["high"]),
                    low=Decimal(row["low"]), close=Decimal(row["close"]),
                    volume=Decimal(row["volume"]),
                )
                with self.session_factory() as s:
                    exists = s.get(Bar, (symbol, self.timeframe, ts))
                    if exists:
                        continue
                    s.add(bar)
                    s.commit()
                await self.bus.publish(CH_BAR_CLOSED, BarClosed(
                    symbol=symbol, timeframe=self.timeframe, ts=ts,
                    open=bar.open, high=bar.high, low=bar.low,
                    close=bar.close, volume=bar.volume,
                ))
                inserted += 1
        return inserted


async def binance_fetch_klines(symbol: str, timeframe: str, limit: int) -> list[dict]:
    """Live Binance kline fetcher. Not used in tests."""
    import httpx
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": timeframe, "limit": limit}
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
    return [
        {"open_time": row[0], "open": row[1], "high": row[2],
         "low": row[3], "close": row[4], "volume": row[5]}
        for row in r.json()
    ]
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest tests/test_data_ingestor.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/loophedge/services/__init__.py src/loophedge/services/data_ingestor.py tests/test_data_ingestor.py
git commit -m "feat: data ingestor pulls binance bars, dedupes, publishes bar.closed"
```

---

### Task 8: Executor service

**Files:**
- Create: `src/loophedge/services/executor.py`
- Modify: `tests/conftest.py:1` — add shared fixtures.
- Create: `tests/test_executor.py`

**Interfaces:**
- Produces: `class Executor` with constructor `(bus: Bus, session_factory, simulator: Simulator, latest_prices: dict[str, Decimal])`. Method `async handle_verified(signal_payload: SignalVerified, candidate: SignalCandidate) -> Fill | None`. Returns `None` if pre-trade caps reject. Updates `Signal.status` and writes `Fill` + `Position` rows.
- Reads current strategy_allocations from open positions / equity.
- Calls `loophedge.risk.caps.enforce_pretrade` before any fill.

- [ ] **Step 1: Update `tests/conftest.py`**

```python
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from loophedge.models import Base


@pytest.fixture
def starting_capital() -> Decimal:
    return Decimal("100000")


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)
```

- [ ] **Step 2: Write failing tests**

`tests/test_executor.py`:
```python
from datetime import UTC, datetime
from decimal import Decimal

import fakeredis.aioredis
import pytest

from loophedge.bus import Bus
from loophedge.ledger.simulator import Simulator
from loophedge.models import Fill, Signal
from loophedge.schemas import SignalCandidate, SignalVerified
from loophedge.services.executor import Executor


def _candidate(size_pct="0.02", strat="momentum_btc"):
    return SignalCandidate(signal_id="sig1", strategy_id=strat,
                            symbol="BTCUSDT", side="long",
                            size_pct=Decimal(size_pct),
                            reasoning="t")


@pytest.mark.asyncio
async def test_approved_signal_creates_fill(session_factory, starting_capital):
    with session_factory() as s:
        s.add(Signal(id="sig1", strategy_id="momentum_btc", symbol="BTCUSDT",
                     side="long", size_pct=Decimal("0.02"), status="approved",
                     maker_payload={}))
        s.commit()
    sim = Simulator(starting_cash=starting_capital)
    ex = Executor(Bus(fakeredis.aioredis.FakeRedis()), session_factory, sim,
                  latest_prices={"BTCUSDT": Decimal("60000")})
    fill = await ex.handle_verified(
        SignalVerified(signal_id="sig1", verdict="approve"),
        _candidate(),
    )
    assert fill is not None
    with session_factory() as s:
        assert s.query(Fill).count() == 1
        assert s.get(Signal, "sig1").status == "executed"


@pytest.mark.asyncio
async def test_oversized_signal_rejected_by_caps(session_factory, starting_capital):
    with session_factory() as s:
        s.add(Signal(id="sig1", strategy_id="m", symbol="BTCUSDT", side="long",
                     size_pct=Decimal("0.10"), status="approved", maker_payload={}))
        s.commit()
    sim = Simulator(starting_cash=starting_capital)
    ex = Executor(Bus(fakeredis.aioredis.FakeRedis()), session_factory, sim,
                  latest_prices={"BTCUSDT": Decimal("60000")})
    fill = await ex.handle_verified(
        SignalVerified(signal_id="sig1", verdict="approve"),
        _candidate(size_pct="0.10"),
    )
    assert fill is None
    with session_factory() as s:
        sig = s.get(Signal, "sig1")
        assert sig.status == "killed"
        assert "position size" in sig.rejection_reason.lower()
```

- [ ] **Step 3: Run tests — expect ImportError**

Run: `pytest tests/test_executor.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement `src/loophedge/services/executor.py`**

```python
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from loophedge.bus import Bus
from loophedge.ledger.simulator import Simulator
from loophedge.models import Fill as FillRow
from loophedge.models import Position as PositionRow
from loophedge.models import Signal
from loophedge.risk.caps import ProposedTrade, enforce_pretrade
from loophedge.schemas import SignalCandidate, SignalVerified


class Executor:
    def __init__(self, bus: Bus, session_factory: sessionmaker,
                 simulator: Simulator, latest_prices: dict[str, Decimal]):
        self.bus = bus
        self.session_factory = session_factory
        self.simulator = simulator
        self.latest_prices = latest_prices

    async def handle_verified(self, verified: SignalVerified,
                              candidate: SignalCandidate) -> FillRow | None:
        equity = self.simulator.equity(self.latest_prices)
        allocations = self._current_strategy_allocations(equity)

        verdict = enforce_pretrade(
            equity=equity,
            current_positions={s: p.qty for s, p in self.simulator.positions.items()},
            strategy_allocations=allocations,
            proposed=ProposedTrade(strategy_id=candidate.strategy_id,
                                    symbol=candidate.symbol,
                                    side=candidate.side,
                                    size_pct=candidate.size_pct),
        )

        if not verdict.allowed:
            self._mark_signal(verified.signal_id, "killed", verdict.reason)
            return None

        ref_price = self.latest_prices[candidate.symbol]
        notional = equity * candidate.size_pct
        qty = notional / ref_price

        fill = self.simulator.apply_fill(
            symbol=candidate.symbol, side=candidate.side, qty=qty,
            ref_price=ref_price, ts=datetime.now(UTC),
        )

        with self.session_factory() as s:
            row = FillRow(id=fill.id, signal_id=verified.signal_id, ts=fill.ts,
                          symbol=fill.symbol, side=fill.side, qty=fill.qty,
                          price=fill.price, fees=fill.fees, venue="simulator")
            s.add(row)
            sig = s.get(Signal, verified.signal_id)
            if sig:
                sig.status = "executed"
            pos = s.get(PositionRow, candidate.symbol)
            new_pos = self.simulator.positions[candidate.symbol]
            if pos is None:
                s.add(PositionRow(symbol=candidate.symbol, qty=new_pos.qty,
                                   avg_entry=new_pos.avg_entry,
                                   unrealized_pnl=Decimal("0"),
                                   updated_at=fill.ts))
            else:
                pos.qty = new_pos.qty
                pos.avg_entry = new_pos.avg_entry
                pos.updated_at = fill.ts
            s.commit()
        return row

    def _current_strategy_allocations(self, equity: Decimal) -> dict[str, Decimal]:
        # Deduplicate by (strategy_id, symbol) so a strategy with multiple
        # executed signals on the same symbol counts that position only once.
        owned: dict[str, set[str]] = {}
        with self.session_factory() as s:
            executed = s.query(Signal).filter(Signal.status == "executed").all()
            for sig in executed:
                owned.setdefault(sig.strategy_id, set()).add(sig.symbol)
        out: dict[str, Decimal] = {}
        for strategy_id, symbols in owned.items():
            total = Decimal("0")
            for symbol in symbols:
                pos = self.simulator.positions.get(symbol)
                if pos is None or pos.qty == 0:
                    continue
                ref = self.latest_prices.get(symbol, pos.avg_entry)
                total += abs(pos.qty) * ref
            out[strategy_id] = total / equity if equity else Decimal("0")
        return out

    def _mark_signal(self, signal_id: str, status: str, reason: str) -> None:
        with self.session_factory() as s:
            sig = s.get(Signal, signal_id)
            if sig:
                sig.status = status
                sig.rejection_reason = reason
                s.commit()
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest tests/test_executor.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/loophedge/services/executor.py tests/test_executor.py tests/conftest.py
git commit -m "feat: executor enforces pre-trade caps and writes fills"
```

---

### Task 9: Risk monitor service

**Files:**
- Create: `src/loophedge/services/risk_monitor.py`
- Create: `tests/test_risk_monitor.py`

**Interfaces:**
- Produces: `class RiskMonitor` with constructor `(bus: Bus, session_factory, kill_dd_pct: Decimal)`. Method `async tick(now: datetime, current_equity: Decimal) -> CircuitBroken | None`. Tracks 30-day rolling high in Postgres via `equity_snapshots`. Inserts a snapshot every tick. If `drawdown_pct >= kill_dd_pct`, publishes `CircuitBroken` and inserts a `RiskEvent`.

- [ ] **Step 1: Write failing tests**

`tests/test_risk_monitor.py`:
```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import fakeredis.aioredis
import pytest

from loophedge.bus import Bus
from loophedge.models import EquitySnapshot, RiskEvent
from loophedge.services.risk_monitor import RiskMonitor


@pytest.mark.asyncio
async def test_no_kill_within_threshold(session_factory):
    rm = RiskMonitor(Bus(fakeredis.aioredis.FakeRedis()), session_factory,
                     kill_dd_pct=Decimal("0.15"))
    now = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    assert await rm.tick(now, Decimal("100000")) is None
    assert await rm.tick(now + timedelta(minutes=1), Decimal("95000")) is None  # 5% dd
    with session_factory() as s:
        assert s.query(EquitySnapshot).count() == 2


@pytest.mark.asyncio
async def test_kill_fires_on_threshold(session_factory):
    rm = RiskMonitor(Bus(fakeredis.aioredis.FakeRedis()), session_factory,
                     kill_dd_pct=Decimal("0.15"))
    now = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    assert await rm.tick(now, Decimal("100000")) is None
    event = await rm.tick(now + timedelta(minutes=1), Decimal("84500"))  # 15.5% dd
    assert event is not None
    assert event.action == "flatten_all"
    with session_factory() as s:
        assert s.query(RiskEvent).count() == 1


@pytest.mark.asyncio
async def test_rolling_high_window_is_30_days(session_factory):
    rm = RiskMonitor(Bus(fakeredis.aioredis.FakeRedis()), session_factory,
                     kill_dd_pct=Decimal("0.15"))
    old = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    now = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    await rm.tick(old, Decimal("200000"))  # old high should NOT count
    assert await rm.tick(now, Decimal("100000")) is None  # 100k is the new high
    # 15% below new high = 85k → still no kill at 90k
    assert await rm.tick(now + timedelta(minutes=1), Decimal("90000")) is None
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_risk_monitor.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `src/loophedge/services/risk_monitor.py`**

```python
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from loophedge.bus import CH_CIRCUIT_BROKEN, Bus
from loophedge.models import EquitySnapshot, RiskEvent
from loophedge.schemas import CircuitBroken


class RiskMonitor:
    def __init__(self, bus: Bus, session_factory: sessionmaker, kill_dd_pct: Decimal):
        self.bus = bus
        self.session_factory = session_factory
        self.kill_dd_pct = kill_dd_pct

    async def tick(self, now: datetime, current_equity: Decimal) -> CircuitBroken | None:
        window_start = now - timedelta(days=30)
        with self.session_factory() as s:
            recent_high_row = s.execute(
                select(EquitySnapshot.equity)
                .where(EquitySnapshot.ts >= window_start)
                .order_by(EquitySnapshot.equity.desc())
                .limit(1)
            ).scalar()
            rolling_high = max(recent_high_row or Decimal("0"), current_equity)

            dd = (rolling_high - current_equity) / rolling_high if rolling_high else Decimal("0")
            s.add(EquitySnapshot(ts=now, cash=Decimal("0"),
                                  equity=current_equity, drawdown_pct=dd))
            s.commit()

            if dd >= self.kill_dd_pct:
                event_payload = {"drawdown_pct": str(dd), "equity": str(current_equity),
                                  "rolling_high": str(rolling_high)}
                s.add(RiskEvent(ts=now, kind="circuit_broken",
                                 payload=event_payload,
                                 actions_taken={"action": "flatten_all"}))
                s.commit()
                event = CircuitBroken(ts=now, drawdown_pct=dd, action="flatten_all")
                await self.bus.publish(CH_CIRCUIT_BROKEN, event)
                return event
        return None
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_risk_monitor.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/loophedge/services/risk_monitor.py tests/test_risk_monitor.py
git commit -m "feat: risk monitor enforces 15% drawdown kill switch on 30-day rolling high"
```

---

### Task 10: Dashboard service (FastAPI)

**Files:**
- Create: `src/loophedge/services/dashboard.py`
- Create: `tests/test_dashboard.py`

**Interfaces:**
- Produces: FastAPI `app` with routes `GET /health` → `{"status": "ok"}`, `GET /api/equity` → list of `{ts, equity, drawdown_pct}`, `GET /api/positions` → list of `{symbol, qty, avg_entry, unrealized_pnl}`, `GET /api/signals?limit=50` → recent signals with status, `GET /api/risk-events` → recent risk events. Plain HTML `GET /` rendered via Jinja2 inline template with HTMX polling the API.

- [ ] **Step 1: Write failing tests**

`tests/test_dashboard.py`:
```python
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from loophedge.models import EquitySnapshot, Position
from loophedge.services.dashboard import build_app


def test_health(session_factory):
    app = build_app(session_factory)
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_equity_endpoint(session_factory):
    with session_factory() as s:
        s.add(EquitySnapshot(ts=datetime.now(UTC), cash=Decimal("100000"),
                              equity=Decimal("100000"), drawdown_pct=Decimal("0")))
        s.commit()
    app = build_app(session_factory)
    client = TestClient(app)
    r = client.get("/api/equity")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_positions_endpoint(session_factory):
    with session_factory() as s:
        s.add(Position(symbol="BTCUSDT", qty=Decimal("0.1"),
                       avg_entry=Decimal("60000"),
                       unrealized_pnl=Decimal("0"),
                       updated_at=datetime.now(UTC)))
        s.commit()
    app = build_app(session_factory)
    client = TestClient(app)
    r = client.get("/api/positions")
    assert r.status_code == 200
    assert r.json()[0]["symbol"] == "BTCUSDT"


def test_root_returns_html(session_factory):
    app = build_app(session_factory)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "loop-hedge" in r.text.lower()
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_dashboard.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `src/loophedge/services/dashboard.py`**

```python
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from loophedge.models import EquitySnapshot, Position, RiskEvent, Signal

_TEMPLATE = """\
<!doctype html>
<html><head><title>loop-hedge</title>
<script src="https://unpkg.com/htmx.org@2.0.3"></script>
<style>body{font-family:system-ui;margin:2em;background:#0a0a0a;color:#eee}
table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #333;padding:6px 10px;text-align:left}
.card{background:#161616;padding:1em;margin:1em 0;border:1px solid #333}</style>
</head><body>
<h1>loop-hedge</h1>
<div class="card"><h2>Equity</h2>
<div hx-get="/api/equity" hx-trigger="load, every 5s" hx-swap="innerHTML"></div></div>
<div class="card"><h2>Positions</h2>
<div hx-get="/api/positions" hx-trigger="load, every 5s" hx-swap="innerHTML"></div></div>
<div class="card"><h2>Recent signals</h2>
<div hx-get="/api/signals" hx-trigger="load, every 5s" hx-swap="innerHTML"></div></div>
</body></html>"""


def build_app(session_factory: sessionmaker) -> FastAPI:
    app = FastAPI(title="loop-hedge")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def root():
        return _TEMPLATE

    @app.get("/api/equity")
    def equity():
        with session_factory() as s:
            rows = s.execute(
                select(EquitySnapshot).order_by(EquitySnapshot.ts.desc()).limit(200)
            ).scalars().all()
            return [{"ts": r.ts.isoformat(), "equity": str(r.equity),
                     "drawdown_pct": str(r.drawdown_pct)} for r in rows]

    @app.get("/api/positions")
    def positions():
        with session_factory() as s:
            rows = s.execute(select(Position)).scalars().all()
            return [{"symbol": r.symbol, "qty": str(r.qty),
                     "avg_entry": str(r.avg_entry),
                     "unrealized_pnl": str(r.unrealized_pnl)} for r in rows]

    @app.get("/api/signals")
    def signals(limit: int = 50):
        with session_factory() as s:
            rows = s.execute(
                select(Signal).order_by(Signal.ts_created.desc()).limit(limit)
            ).scalars().all()
            return [{"id": r.id, "strategy_id": r.strategy_id, "symbol": r.symbol,
                     "side": r.side, "size_pct": str(r.size_pct),
                     "status": r.status,
                     "rejection_reason": r.rejection_reason} for r in rows]

    @app.get("/api/risk-events")
    def risk_events():
        with session_factory() as s:
            rows = s.execute(
                select(RiskEvent).order_by(RiskEvent.ts.desc()).limit(50)
            ).scalars().all()
            return [{"id": r.id, "ts": r.ts.isoformat(), "kind": r.kind,
                     "payload": r.payload, "actions_taken": r.actions_taken}
                    for r in rows]

    return app
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_dashboard.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/loophedge/services/dashboard.py tests/test_dashboard.py
git commit -m "feat: read-only fastapi dashboard with equity, positions, signals"
```

---

### Task 11: CLI entrypoint

**Files:**
- Create: `src/loophedge/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Produces: `main(argv: list[str]) -> int`. Subcommands: `ingest`, `execute`, `risk`, `dashboard`. Each subcommand wires up its service with real dependencies (Postgres, Redis, Binance fetcher) and runs its own loop. The CLI is invoked inside each container as `python -m loophedge <service>`.
- For Phase 0–1 tests, we test only the dispatch + arg parsing, not the long-running loops.

- [ ] **Step 1: Write failing tests**

`tests/test_cli.py`:
```python
import pytest

from loophedge.cli import main


def test_unknown_subcommand_returns_nonzero():
    assert main(["nope"]) != 0


def test_dashboard_subcommand_dispatch(monkeypatch):
    called = {}
    def fake_run_dashboard():
        called["yes"] = True
    monkeypatch.setattr("loophedge.cli.run_dashboard", fake_run_dashboard)
    assert main(["dashboard"]) == 0
    assert called == {"yes": True}


def test_ingest_subcommand_dispatch(monkeypatch):
    called = {}
    def fake_run_ingest():
        called["yes"] = True
    monkeypatch.setattr("loophedge.cli.run_ingest", fake_run_ingest)
    assert main(["ingest"]) == 0
    assert called == {"yes": True}
```

- [ ] **Step 2: Run tests — expect ImportError**

Run: `pytest tests/test_cli.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `src/loophedge/cli.py`**

```python
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
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_cli.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/loophedge/cli.py tests/test_cli.py
git commit -m "feat: cli entrypoint dispatching service runners"
```

---

### Task 12: End-to-end replay test

**Files:**
- Create: `tests/test_e2e_replay.py`

**Interfaces:**
- Consumes: everything from Tasks 1–11.
- Produces: a single integration test that wires data ingestor → executor → risk monitor with in-memory SQLite + fakeredis, feeds a synthetic 30-bar sequence with a known PnL outcome, and asserts: bars persisted, hardcoded signals filled (or capped), positions correct, no malformed schema, kill switch behaves on injected drawdown.

- [ ] **Step 1: Write the test**

`tests/test_e2e_replay.py`:
```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import fakeredis.aioredis
import pytest

from loophedge.bus import Bus
from loophedge.ledger.simulator import Simulator
from loophedge.models import Bar, Fill, Position, Signal
from loophedge.schemas import SignalCandidate, SignalVerified
from loophedge.services.data_ingestor import DataIngestor
from loophedge.services.executor import Executor
from loophedge.services.risk_monitor import RiskMonitor


@pytest.mark.asyncio
async def test_full_replay_pipeline(session_factory, starting_capital):
    """30 bars, one buy at bar 5, one sell at bar 25, drawdown injected at bar 28."""
    base_ts = 1_700_000_000_000  # ms
    rows = [
        {"open_time": base_ts + i * 300_000,
         "open": str(60000 + i * 100), "high": str(60100 + i * 100),
         "low": str(59900 + i * 100), "close": str(60050 + i * 100),
         "volume": "1.0"}
        for i in range(30)
    ]

    async def fake_klines(*_):
        return rows

    bus = Bus(fakeredis.aioredis.FakeRedis())
    ing = DataIngestor(bus, session_factory, fake_klines, ["BTCUSDT"], "5m")
    assert await ing.fetch_and_publish_once() == 30

    sim = Simulator(starting_cash=starting_capital)
    latest_prices = {"BTCUSDT": Decimal("60500")}  # mid of bar 5
    ex = Executor(bus, session_factory, sim, latest_prices)

    with session_factory() as s:
        s.add(Signal(id="buy1", strategy_id="momentum_btc", symbol="BTCUSDT",
                     side="long", size_pct=Decimal("0.02"), status="approved",
                     maker_payload={}))
        s.commit()

    fill = await ex.handle_verified(
        SignalVerified(signal_id="buy1", verdict="approve"),
        SignalCandidate(signal_id="buy1", strategy_id="momentum_btc",
                         symbol="BTCUSDT", side="long",
                         size_pct=Decimal("0.02"), reasoning="entry"),
    )
    assert fill is not None
    with session_factory() as s:
        assert s.query(Fill).count() == 1
        assert s.get(Position, "BTCUSDT").qty > 0

    rm = RiskMonitor(bus, session_factory, kill_dd_pct=Decimal("0.15"))
    now = datetime.now(UTC)
    await rm.tick(now, starting_capital)                   # baseline
    event = await rm.tick(now + timedelta(minutes=1),
                           starting_capital * Decimal("0.80"))  # 20% dd
    assert event is not None
    assert event.action == "flatten_all"

    with session_factory() as s:
        bar_count = s.query(Bar).count()
        sig_count = s.query(Signal).filter(Signal.status == "executed").count()
        pos = s.get(Position, "BTCUSDT")
    assert bar_count == 30
    assert sig_count == 1
    assert pos.qty > 0
```

- [ ] **Step 2: Run the test — expect PASS**

Run: `pytest tests/test_e2e_replay.py -v`
Expected: 1 passed.

- [ ] **Step 3: Run the full test suite**

Run: `pytest -v`
Expected: all green (~25+ tests).

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e_replay.py
git commit -m "test: end-to-end replay covering ingestor, executor, risk monitor"
```

---

### Task 13: Dockerfile + docker-compose

**Files:**
- Create: `docker/base.Dockerfile`
- Create: `docker-compose.yml`
- Create: `docker/README.md`

**Interfaces:**
- Produces: a single image `loophedge:dev` reused by every service. `docker-compose.yml` defines 8 services (postgres, redis, dashboard, data-ingestor, executor, risk-monitor, maker-agent, checker-agent — last two stubbed) using `command:` to pick which CLI subcommand each container runs.
- For Phase 0–1 the maker/checker/genesis containers are defined but their commands are `sleep infinity` placeholders. They are wired into the compose graph so Phase 2 only needs to swap the command.

- [ ] **Step 1: Create `docker/base.Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml ./
RUN pip install -e ".[dev]" || pip install .

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

ENTRYPOINT ["python", "-m", "loophedge"]
```

- [ ] **Step 2: Create `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: loophedge
      POSTGRES_PASSWORD: loophedge
      POSTGRES_DB: loophedge
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U loophedge"]
      interval: 5s
      timeout: 3s
      retries: 5

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  loophedge-base:
    build:
      context: .
      dockerfile: docker/base.Dockerfile
    image: loophedge:dev
    profiles: ["build-only"]

  data-ingestor:
    image: loophedge:dev
    command: ["ingest"]
    env_file: .env
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }

  executor:
    image: loophedge:dev
    command: ["execute"]
    env_file: .env
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }

  risk-monitor:
    image: loophedge:dev
    command: ["risk"]
    env_file: .env
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }

  dashboard:
    image: loophedge:dev
    command: ["dashboard"]
    env_file: .env
    ports:
      - "8000:8000"
    depends_on:
      postgres: { condition: service_healthy }

  maker-agent:
    image: loophedge:dev
    entrypoint: ["sleep", "infinity"]      # Phase 2

  checker-agent:
    image: loophedge:dev
    entrypoint: ["sleep", "infinity"]      # Phase 2

  strategy-genesis-agent:
    image: loophedge:dev
    entrypoint: ["sleep", "infinity"]      # Phase 2

volumes:
  pg_data:
```

- [ ] **Step 3: Create `docker/README.md`**

```markdown
# Docker setup

One image `loophedge:dev` runs every service; the `command:` field in
`docker-compose.yml` selects the CLI subcommand.

## Build only

```bash
docker compose build loophedge-base
```

## Run (developer's choice — not automated by plan execution)

```bash
docker compose up postgres redis dashboard data-ingestor
```

Maker / checker / genesis agents are placeholders until Phase 2.
```

- [ ] **Step 4: Verify the image builds**

Run: `docker compose build loophedge-base`
Expected: build succeeds, image `loophedge:dev` exists in `docker images`.

(If docker is unavailable in the sandbox, skip this step and document the
gap in the commit message — the implementer will verify on the VPS.)

- [ ] **Step 5: Commit**

```bash
git add docker/ docker-compose.yml
git commit -m "feat: docker base image and compose graph with phase-2 agent stubs"
```

---

### Task 14: Final verification

- [ ] **Step 1: Run full test suite with coverage**

Run: `pytest --cov=src/loophedge --cov-report=term-missing`
Expected: all tests pass; coverage on `src/loophedge/{risk,ledger,services,bus,schemas,config,models,cli}` ≥ 85%.

- [ ] **Step 2: Lint and type-check**

Run: `ruff check src tests && mypy src/loophedge`
Expected: no errors. Fix any.

- [ ] **Step 3: Commit any lint/type fixes**

```bash
git add -A
git commit -m "chore: lint and type-check pass" || echo "nothing to commit"
```

- [ ] **Step 4: Tag the Phase 1 milestone**

```bash
git tag -a phase-1-deterministic-core -m "Phase 0+1 complete: deterministic core with self-sim ledger, ready for agent layer"
```

---

## What's NOT in this plan (Phase 2+)

- Claude Agent SDK integration (maker, checker, strategy-genesis containers).
- The `skills/` versioned volume + git-committed lesson trail.
- The `state/` file-backed memory volume (`STATE.md`, `LESSONS.md`, traces). No agents in Phase 1 → no agent memory yet.
- Wiring the executor to subscribe to `circuit.broken` and auto-flatten positions. Phase 1 verifies the kill switch *publishes* the event and persists a `RiskEvent`; the auto-flatten consumer is built when the executor becomes a long-running service alongside the maker/checker loops in Phase 2.
- Long-running event-driven executor (Phase 1 invokes `Executor.handle_verified()` via library calls in tests; Phase 2 will run it as a subscriber to `signal.verified`).
- Binance Spot Testnet live order path.
- Strategy registry + active/pending/retired lifecycle.
- Backtest engine that the checker agent uses.
- Real trading strategies — Phase 1 only proves the plumbing works.

These will get their own implementation plan after Phase 1 is reviewed and shipped.
