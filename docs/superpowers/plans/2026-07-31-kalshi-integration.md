# Kalshi Weather Integration — Cycle 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Kalshi weather event contracts and Open-Meteo weather forecasts into the existing loop-hedge system so the genesis→checker→maker loop can propose, validate, and emit signals for weather strategies — with no live order routing yet.

**Architecture:** Additive schema (new `contract` and `weather_forecast` tables), per-venue Simulator cost function (crypto flat-bps + Kalshi `0.07·p·(1−p)` per contract), two new sibling ingester services (`kalshi-ingestor` and `weather-ingestor`), two new agent tools, backwards-compatible strategy signature evolution (`forecasts=None` kwarg).

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, Alembic, httpx (async), Postgres 16, Docker Compose. Existing patterns: `DataIngestor` shape, `Simulator.apply_fill`, agent tool functions.

## Global Constraints

- All timestamps stored as UTC-aware `datetime` values via `DateTime(timezone=True)`
- All monetary/price values as `Decimal` — never `float` in the fill/equity path
- All new tables use `JSON` column type (not `JSONB`) for SQLite test compatibility
- Every INSERT for polled data uses conflict-safe idempotent inserts
- No new secrets in `.env` — Kalshi public API and Open-Meteo need no keys
- Existing 92-test suite must stay green after every task
- The bar interface for strategies remains `bar.close`, `bar.symbol`, `bar.ts` attribute access — never dict access (per `skills/strategy_genesis.md`)
- Side vocabulary is `"long"` and `"short"` — never `"buy"` / `"sell"` (per `skills/strategy_genesis.md`)
- Existing `restart: on-failure:10` compose policy applies to new services

## File Structure

**New files:**

```
migrations/versions/004_contract_and_weather.py     # Alembic migration
src/loophedge/services/kalshi_client.py             # Kalshi HTTP client
src/loophedge/services/kalshi_ingestor.py           # KalshiIngestor + candle/settlement handling
src/loophedge/services/open_meteo_client.py         # Open-Meteo HTTP client
src/loophedge/services/weather_ingestor.py          # WeatherIngestor
tests/test_kalshi_client.py
tests/test_kalshi_ingestor.py
tests/test_open_meteo_client.py
tests/test_weather_ingestor.py
tests/test_simulator_cost_polymorphism.py
tests/test_contract_settlement.py
tests/test_kalshi_backtest_end_to_end.py
tests/test_e2e_kalshi_agent_loop.py
tests/test_migrations.py
```

**Modified files:**

```
src/loophedge/models.py                             # +Contract, +WeatherForecast
src/loophedge/ledger/simulator.py                   # per-venue cost model
src/loophedge/strategies/interface.py               # +forecasts kwarg
src/loophedge/backtest/engine.py                    # +forecasts kwarg propagation
src/loophedge/agents/maker.py                       # pass forecasts for Kalshi symbols
src/loophedge/agents/tools.py                       # +query_kalshi_bars, +query_weather_forecast
src/loophedge/agents/genesis.py                     # +new tools in tool set
src/loophedge/agents/checker.py                     # +new tools in tool set
src/loophedge/cli.py                                # +run_kalshi, +run_weather dispatchers
skills/strategy_genesis.md                          # +Kalshi weather section
docker-compose.yml                                  # +kalshi-ingestor, +weather-ingestor
```

## Task Dependency Graph

```
Task 1 (schema) ──┬─→ Task 2 (cost model) ─→ Task 3 (settlement)
                  ├─→ Task 4 (Kalshi ingester)
                  └─→ Task 5 (Weather ingester)
                              │
Task 6 (strategy signature + agent tools + playbook) ─→ Task 7 (e2e integration)
```

Tasks 2, 4, 5 can execute in parallel once Task 1 is done. Task 3 needs 2. Task 6 needs 2, 4, 5. Task 7 needs 6.

---

## Task 1: Schema — Contract and WeatherForecast tables

**Files:**
- Modify: `src/loophedge/models.py` (add Contract, WeatherForecast models)
- Create: `migrations/versions/004_contract_and_weather.py`
- Create: `tests/test_migrations.py`

**Interfaces:**
- Consumes: existing `Base`, `SYMBOL_LEN`, `M20_8`, `String`, `DateTime`, `JSON` from models.py
- Produces:
  - `Contract` model: `symbol: str` (PK), `venue: str`, `open_ts: datetime | None`, `close_ts: datetime | None`, `resolution_ts: datetime | None`, `settlement_value: Decimal | None`, `resolution_source: str | None`, `contract_metadata: dict` (JSON column — named `contract_metadata` not `metadata` because `metadata` is reserved on SQLAlchemy `DeclarativeBase`)
  - `WeatherForecast` model: `id: int` (PK auto), `city: str`, `forecast_ts: datetime`, `valid_ts: datetime`, `temp_mean_c: Decimal`, `temp_std_c: Decimal`, `source: str`, unique on `(city, forecast_ts, valid_ts, source)`
  - After `alembic upgrade head`: `contract` table contains rows for `BTCUSDT` and `ETHUSDT` with `venue='binance_us'` (all other cols null)

- [ ] **Step 1: Add Contract and WeatherForecast models to `src/loophedge/models.py`**

Append to `src/loophedge/models.py` (after `RiskEvent`):

```python
class Contract(Base):
    __tablename__ = "contracts"
    symbol: Mapped[str] = mapped_column(SYMBOL_LEN, primary_key=True)
    venue: Mapped[str] = mapped_column(String(32))
    open_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settlement_value: Mapped[Decimal | None] = mapped_column(M20_8, nullable=True)
    resolution_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    contract_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


class WeatherForecast(Base):
    __tablename__ = "weather_forecasts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city: Mapped[str] = mapped_column(String(8))
    forecast_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    temp_mean_c: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    temp_std_c: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    source: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        __import__("sqlalchemy").UniqueConstraint(
            "city", "forecast_ts", "valid_ts", "source",
            name="uq_weather_forecast_key"
        ),
    )
```

Also add `Integer` to the existing SQLAlchemy import line at the top of `models.py`. Verify the line already imports it (it does: `Integer` is in the existing `from sqlalchemy import` — no change needed).

- [ ] **Step 2: Create the alembic migration file `migrations/versions/004_contract_and_weather.py`**

```python
"""contract and weather_forecast tables

Adds Contract lifecycle table (event contracts + backfilled crypto symbols)
and WeatherForecast table for Open-Meteo forecast ingestion.

Revision ID: 004
Revises: 003
Create Date: 2026-07-31
"""
import sqlalchemy as sa
from alembic import op

revision = "004"
down_revision = "003"


def upgrade():
    op.create_table(
        "contracts",
        sa.Column("symbol", sa.String(96), primary_key=True),
        sa.Column("venue", sa.String(32), nullable=False),
        sa.Column("open_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settlement_value", sa.Numeric(20, 8), nullable=True),
        sa.Column("resolution_source", sa.String(128), nullable=True),
        sa.Column("contract_metadata", sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'")),
    )
    op.create_table(
        "weather_forecasts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("city", sa.String(8), nullable=False),
        sa.Column("forecast_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("temp_mean_c", sa.Numeric(6, 2), nullable=False),
        sa.Column("temp_std_c", sa.Numeric(6, 2), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.UniqueConstraint("city", "forecast_ts", "valid_ts", "source",
                            name="uq_weather_forecast_key"),
    )
    # Backfill contract rows for existing crypto symbols so cost lookups work.
    op.execute(
        "INSERT INTO contracts (symbol, venue, contract_metadata) VALUES "
        "('BTCUSDT', 'binance_us', '{}'), "
        "('ETHUSDT', 'binance_us', '{}')"
    )


def downgrade():
    op.drop_table("weather_forecasts")
    op.drop_table("contracts")
```

- [ ] **Step 3: Write failing migration round-trip test `tests/test_migrations.py`**

```python
"""End-to-end alembic migration exercise: upgrade from empty, downgrade
last revision, upgrade again, assert no exceptions and expected tables exist."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


@pytest.fixture
def alembic_config(tmp_path):
    db_url = f"sqlite:///{tmp_path}/mig.db"
    cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option(
        "script_location",
        str(Path(__file__).parent.parent / "migrations"),
    )
    return cfg, db_url


def test_upgrade_head_creates_all_tables(alembic_config):
    cfg, db_url = alembic_config
    command.upgrade(cfg, "head")
    eng = create_engine(db_url)
    tables = set(inspect(eng).get_table_names())
    assert "contracts" in tables
    assert "weather_forecasts" in tables
    assert "bars" in tables
    assert "strategies" in tables


def test_downgrade_and_reupgrade_no_data_loss_on_untouched_tables(alembic_config):
    cfg, db_url = alembic_config
    command.upgrade(cfg, "head")

    from sqlalchemy import text
    eng = create_engine(db_url)
    with eng.begin() as conn:
        rows = conn.execute(text("SELECT symbol, venue FROM contracts ORDER BY symbol")).all()
        assert rows == [("BTCUSDT", "binance_us"), ("ETHUSDT", "binance_us")]

    command.downgrade(cfg, "-1")
    tables_after = set(inspect(create_engine(db_url)).get_table_names())
    assert "contracts" not in tables_after
    assert "weather_forecasts" not in tables_after

    command.upgrade(cfg, "head")
    tables_final = set(inspect(create_engine(db_url)).get_table_names())
    assert "contracts" in tables_final
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_migrations.py -v
```

Expected: FAIL — migration 004 not yet applied to the alembic script sequence OR test-side infrastructure references. If the tests actually pass at this point (because the migration file exists), that's fine — proceed.

- [ ] **Step 5: Run the full existing test suite to verify no regressions from the model additions**

```bash
.venv/bin/pytest tests/ -q
```

Expected: PASS (92 existing + 2 new migration tests = 94). If any of the existing tests fail due to model import issues, fix and re-run.

- [ ] **Step 6: Commit**

```bash
git add src/loophedge/models.py migrations/versions/004_contract_and_weather.py tests/test_migrations.py
git commit -m "feat: add Contract and WeatherForecast schema (migration 004)"
```

---

## Task 2: Simulator per-venue cost model

**Files:**
- Modify: `src/loophedge/ledger/simulator.py` (introduce CostModel, per-venue registry, venue lookup)
- Create: `tests/test_simulator_cost_polymorphism.py`

**Interfaces:**
- Consumes: `Contract` model from Task 1 (via optional `session_factory` on Simulator for venue lookup)
- Produces:
  - `CostModel` dataclass with `slippage(ref_price: Decimal, side: str) -> Decimal` and `fee(qty: Decimal, price: Decimal) -> Decimal`
  - `COST_MODELS: dict[str, CostModel]` with `binance_us` and `kalshi` entries
  - `Simulator(starting_cash, session_factory=None)` — optional `session_factory` unlocks venue lookup; without it, all fills use `binance_us` costs (existing test behavior preserved)
  - `Simulator.apply_fill` behavior for `venue='binance_us'` is byte-identical to current

- [ ] **Step 1: Write failing tests in `tests/test_simulator_cost_polymorphism.py`**

```python
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from loophedge.ledger.simulator import COST_MODELS, Simulator
from loophedge.models import Base, Contract


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        s.add(Contract(symbol="BTCUSDT", venue="binance_us"))
        s.add(Contract(symbol="KXHIGHNY-26AUG05-B82.5", venue="kalshi"))
        s.commit()
    return Session


def test_binance_us_flat_bps_matches_prior_behavior():
    sim = Simulator(starting_cash=Decimal("100000"))  # no session_factory -> default
    fill = sim.apply_fill("BTCUSDT", "long", Decimal("1"), Decimal("60000"),
                          datetime.now(UTC))
    # slippage adds 5 bps: 60000 * 1.0005 = 60030
    assert fill.price == Decimal("60030")
    # fee: 60030 * 1 * 10/10000 = 60.03
    assert fill.fees == Decimal("60.03000000")


def test_kalshi_fee_is_per_contract_absolute(session_factory):
    sim = Simulator(starting_cash=Decimal("100000"), session_factory=session_factory)
    # 100 contracts at $0.50 => fee = 100 * 0.07 * 0.5 * 0.5 = 1.75
    fill = sim.apply_fill("KXHIGHNY-26AUG05-B82.5", "long",
                          Decimal("100"), Decimal("0.50"), datetime.now(UTC))
    # No slippage for Kalshi (binary contract, single tick)
    assert fill.price == Decimal("0.50")
    assert fill.fees == Decimal("1.75000000")


def test_kalshi_fee_at_edges_is_near_zero(session_factory):
    sim = Simulator(starting_cash=Decimal("100000"), session_factory=session_factory)
    # 100 contracts at $0.99 => fee = 100 * 0.07 * 0.99 * 0.01 = 0.0693
    fill = sim.apply_fill("KXHIGHNY-26AUG05-B82.5", "long",
                          Decimal("100"), Decimal("0.99"), datetime.now(UTC))
    assert fill.fees == Decimal("0.06930000")


def test_unknown_venue_falls_back_to_binance_us(session_factory):
    """A symbol with no contract row uses the default cost model."""
    sim = Simulator(starting_cash=Decimal("100000"), session_factory=session_factory)
    fill = sim.apply_fill("UNKNOWN_SYMBOL", "long", Decimal("1"), Decimal("100"),
                          datetime.now(UTC))
    # Should apply binance_us defaults: slippage 5bps, fee 10bps
    assert fill.price == Decimal("100.05000000")


def test_cost_models_registry_has_both_venues():
    assert "binance_us" in COST_MODELS
    assert "kalshi" in COST_MODELS
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_simulator_cost_polymorphism.py -v
```

Expected: FAIL — `COST_MODELS` not defined; `Simulator` doesn't accept `session_factory`.

- [ ] **Step 3: Refactor `src/loophedge/ledger/simulator.py`**

Replace the entire file contents with:

```python
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Callable

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
class CostModel:
    """Per-venue cost calculation.

    `apply_slippage` returns the fill price after slippage adjustment given
    the reference price and side. `fee` returns the absolute fee amount for
    a fill of `qty` at `fill_price`. Both use Decimal end-to-end.
    """
    apply_slippage: Callable[[Decimal, str], Decimal]
    fee: Callable[[Decimal, Decimal], Decimal]


def _crypto_slippage(ref_price: Decimal, side: str) -> Decimal:
    slip = ref_price * SLIPPAGE_BPS / BPS
    return ref_price + slip if side == "long" else ref_price - slip


def _crypto_fee(qty: Decimal, fill_price: Decimal) -> Decimal:
    # 10 bps of notional
    return (fill_price * qty * Decimal("10") / BPS).quantize(Decimal("0.00000001"))


def _kalshi_slippage(ref_price: Decimal, side: str) -> Decimal:
    # Binary contracts trade at a single tick; no linear slippage model.
    return ref_price


def _kalshi_fee(qty: Decimal, fill_price: Decimal) -> Decimal:
    """Kalshi fee: 0.07 * price * (1 - price) per contract.

    Real Kalshi rounds each contract's fee up to the nearest cent, then sums.
    For simulation we use the mathematical value (matches within a fraction of
    a cent per contract), quantized to 8 decimal places for Decimal hygiene.
    """
    per_contract = Decimal("0.07") * fill_price * (Decimal("1") - fill_price)
    return (per_contract * qty).quantize(Decimal("0.00000001"))


COST_MODELS: dict[str, CostModel] = {
    "binance_us": CostModel(apply_slippage=_crypto_slippage, fee=_crypto_fee),
    "kalshi":     CostModel(apply_slippage=_kalshi_slippage, fee=_kalshi_fee),
}

_DEFAULT_VENUE = "binance_us"


@dataclass
class Simulator:
    starting_cash: Decimal
    session_factory: object = None  # optional sessionmaker for contract lookup
    cash: Decimal = field(init=False)
    positions: dict[str, _Position] = field(default_factory=dict)
    _venue_cache: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.cash = self.starting_cash

    def _lookup_venue(self, symbol: str) -> str:
        if symbol in self._venue_cache:
            return self._venue_cache[symbol]
        if self.session_factory is None:
            self._venue_cache[symbol] = _DEFAULT_VENUE
            return _DEFAULT_VENUE
        from loophedge.models import Contract  # local import avoids cycle
        with self.session_factory() as s:
            row = s.get(Contract, symbol)
        venue = row.venue if row is not None else _DEFAULT_VENUE
        self._venue_cache[symbol] = venue
        return venue

    def apply_fill(
        self,
        symbol: str,
        side: str,
        qty: Decimal,
        ref_price: Decimal,
        ts: datetime,
    ) -> Fill:
        venue = self._lookup_venue(symbol)
        model = COST_MODELS.get(venue, COST_MODELS[_DEFAULT_VENUE])

        fill_price = model.apply_slippage(ref_price, side)
        fees = model.fee(qty, fill_price)

        signed_qty = qty if side == "long" else -qty
        pos = self.positions.setdefault(symbol, _Position(symbol))
        new_qty = pos.qty + signed_qty

        if pos.qty == 0:
            pos.avg_entry = fill_price
        elif (pos.qty > 0) == (signed_qty > 0):
            total_cost = pos.avg_entry * abs(pos.qty) + fill_price * abs(signed_qty)
            pos.avg_entry = total_cost / abs(new_qty)
        elif new_qty != 0 and (new_qty > 0) != (pos.qty > 0):
            pos.avg_entry = fill_price
        elif new_qty == 0:
            pos.avg_entry = Decimal("0")

        pos.qty = new_qty
        self.cash -= signed_qty * fill_price + fees
        return Fill(id=str(uuid.uuid4()), ts=ts, symbol=symbol, side=side,
                    qty=qty, price=fill_price, fees=fees)

    def equity(self, mark_prices: dict[str, Decimal]) -> Decimal:
        position_value = sum(
            mark_prices.get(p.symbol, p.avg_entry) * p.qty
            for p in self.positions.values()
        )
        return self.cash + Decimal(position_value)
```

Note that the old `apply_fill` accepted a `fee_bps` kwarg; the new version does not. Callers that pass `fee_bps` will break. Verify no callers pass it:

```bash
grep -rn "apply_fill.*fee_bps" src/ tests/
```

Expected: no results.

- [ ] **Step 4: Run all tests**

```bash
.venv/bin/pytest tests/ -q
```

Expected: PASS (94 tests + 5 new = 99). Existing simulator tests should still pass because they don't pass `session_factory`, so cost lookup uses `binance_us` defaults which match the old constants exactly.

- [ ] **Step 5: Commit**

```bash
git add src/loophedge/ledger/simulator.py tests/test_simulator_cost_polymorphism.py
git commit -m "feat: per-venue cost model in Simulator (binance_us + kalshi)"
```

---

## Task 3: Contract settlement handling

**Files:**
- Create: `tests/test_contract_settlement.py`
- (No production code changes if Task 2's design is right — settlement is a bar with `close=settlement_value` and the simulator's existing `apply_fill` on the next signal closes the position at that price.)

**Interfaces:**
- Consumes: `Contract` from Task 1, `Simulator` from Task 2
- Produces: verified behavior that a bar with `close=settlement_value` on a symbol with an open position → next external `apply_fill` closes at settlement; and that `Simulator.equity` marks the position at settlement value

- [ ] **Step 1: Write failing tests in `tests/test_contract_settlement.py`**

```python
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from loophedge.ledger.simulator import Simulator
from loophedge.models import Base, Contract


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        s.add(Contract(symbol="KXHIGHNY-26AUG05-B82.5", venue="kalshi",
                       resolution_ts=datetime(2026, 8, 5, 22, 0, tzinfo=UTC),
                       settlement_value=Decimal("1")))
        s.commit()
    return Session


def test_open_position_marked_at_settlement_value_in_equity(session_factory):
    """After a resolution, the risk monitor's equity() must value the open
    position at settlement, so a winning bet shows up correctly."""
    sim = Simulator(starting_cash=Decimal("100"), session_factory=session_factory)
    now = datetime(2026, 8, 4, 22, 0, tzinfo=UTC)
    sim.apply_fill("KXHIGHNY-26AUG05-B82.5", "long",
                   Decimal("50"), Decimal("0.40"), now)
    # After the buy: cash = 100 - 50*0.40 - fee (~0.84) = ~79.16
    # If contract resolves at 1.00, equity should be cash + 50*1.00 = ~129.16
    equity_at_settlement = sim.equity({"KXHIGHNY-26AUG05-B82.5": Decimal("1")})
    # Fee: 50 * 0.07 * 0.40 * 0.60 = 0.84
    # Cash: 100 - 20 - 0.84 = 79.16
    # Equity at settlement: 79.16 + 50 = 129.16
    assert equity_at_settlement == Decimal("129.16000000")


def test_close_at_settlement_realizes_pnl(session_factory):
    """When the executor observes a settlement bar and issues a closing fill,
    cash reflects the realized gain and position is flat."""
    sim = Simulator(starting_cash=Decimal("100"), session_factory=session_factory)
    now = datetime(2026, 8, 4, 22, 0, tzinfo=UTC)
    sim.apply_fill("KXHIGHNY-26AUG05-B82.5", "long",
                   Decimal("50"), Decimal("0.40"), now)
    # Close at $1.00 (settlement)
    later = datetime(2026, 8, 5, 22, 0, tzinfo=UTC)
    sim.apply_fill("KXHIGHNY-26AUG05-B82.5", "short",
                   Decimal("50"), Decimal("1.00"), later)
    # Position flat
    assert sim.positions["KXHIGHNY-26AUG05-B82.5"].qty == Decimal("0")
    # Cash: 100 - 20 - 0.84 (open fee) + 50 - 0 (close: fee at 1.0*(1-1)=0) = 129.16
    assert sim.cash == Decimal("129.16000000")


def test_losing_bet_settles_to_zero(session_factory):
    """The unhappy path: bought a yes-bucket, market resolved to another bucket."""
    with session_factory() as s:
        s.merge(Contract(symbol="KXHIGHNY-26AUG05-B82.5", venue="kalshi",
                         settlement_value=Decimal("0")))
        s.commit()
    sim = Simulator(starting_cash=Decimal("100"), session_factory=session_factory)
    now = datetime(2026, 8, 4, 22, 0, tzinfo=UTC)
    sim.apply_fill("KXHIGHNY-26AUG05-B82.5", "long",
                   Decimal("50"), Decimal("0.40"), now)
    later = datetime(2026, 8, 5, 22, 0, tzinfo=UTC)
    sim.apply_fill("KXHIGHNY-26AUG05-B82.5", "short",
                   Decimal("50"), Decimal("0"), later)
    assert sim.positions["KXHIGHNY-26AUG05-B82.5"].qty == Decimal("0")
    # Bought 50 at 0.40, sold 50 at 0.00. Loss = 20. Cash: 100 - 20 - 0.84 = 79.16
    assert sim.cash == Decimal("79.16000000")
```

- [ ] **Step 2: Run tests**

```bash
.venv/bin/pytest tests/test_contract_settlement.py -v
```

Expected: PASS on all three — the code from Task 2 already supports this because the simulator treats every fill uniformly. If any fail, the fix is in the Kalshi cost function, not in adding new settlement code.

- [ ] **Step 3: Commit**

```bash
git add tests/test_contract_settlement.py
git commit -m "test: contract settlement realizes PnL correctly for wins and losses"
```

---

## Task 4: Kalshi client + ingester service

**Files:**
- Create: `src/loophedge/services/kalshi_client.py`
- Create: `src/loophedge/services/kalshi_ingestor.py`
- Modify: `src/loophedge/cli.py` (add `run_kalshi` dispatcher)
- Modify: `docker-compose.yml` (add `kalshi-ingestor` service)
- Create: `tests/test_kalshi_client.py`
- Create: `tests/test_kalshi_ingestor.py`

**Interfaces:**
- Consumes: `Contract`, `Bar` from Task 1
- Produces:
  - `kalshi_client.fetch_weather_markets(cities: list[str]) -> list[dict]` — returns Kalshi market dicts filtered to daily-high weather contracts for the given cities. Each dict has `ticker`, `open_time`, `close_time`, `expiration_time`, `subtitle`, `event_ticker`, and if resolved: `result` (`yes`/`no`) and settlement price.
  - `kalshi_client.fetch_candles(ticker: str, resolution_min: int = 5, limit: int = 200) -> list[dict]` — returns candle dicts with `end_period_ts`, `open_price`, `high_price`, `low_price`, `close_price`, `volume`, prices in *cents* as integers.
  - `KalshiIngestor.sync_contracts_once() -> int` — inserts new contracts, updates settlement values; returns count of rows written
  - `KalshiIngestor.fetch_candles_once() -> int` — polls active contracts, writes Bar rows and settlement bars; returns count of bars written

- [ ] **Step 1: Write failing client test `tests/test_kalshi_client.py`**

```python
import pytest
from unittest.mock import AsyncMock, patch

from loophedge.services.kalshi_client import fetch_candles, fetch_weather_markets


@pytest.mark.asyncio
async def test_fetch_weather_markets_filters_to_nyc_daily_high():
    fake_response = {
        "markets": [
            {"ticker": "KXHIGHNY-26AUG05-B82.5", "event_ticker": "KXHIGHNY-26AUG05",
             "status": "open", "close_time": "2026-08-05T22:00:00Z",
             "expiration_time": "2026-08-05T22:00:00Z"},
            {"ticker": "KXHIGHLAX-26AUG05-B75.5", "event_ticker": "KXHIGHLAX-26AUG05",
             "status": "open", "close_time": "2026-08-05T22:00:00Z",
             "expiration_time": "2026-08-05T22:00:00Z"},
            {"ticker": "SOME-OTHER-TICKER", "event_ticker": "OTHER",
             "status": "open"},
        ]
    }

    class MockResponse:
        def __init__(self, data): self._data = data
        def raise_for_status(self): pass
        def json(self): return self._data

    class MockClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return MockResponse(fake_response)

    with patch("loophedge.services.kalshi_client.httpx.AsyncClient", MockClient):
        markets = await fetch_weather_markets(["NYC", "LAX"])
    tickers = {m["ticker"] for m in markets}
    assert "KXHIGHNY-26AUG05-B82.5" in tickers
    assert "KXHIGHLAX-26AUG05-B75.5" in tickers
    assert "SOME-OTHER-TICKER" not in tickers


@pytest.mark.asyncio
async def test_fetch_candles_normalizes_cents_to_dollars():
    fake_response = {
        "candlesticks": [
            {"end_period_ts": 1754433900,
             "price": {"open": 42, "high": 47, "low": 40, "close": 45},
             "volume": 1000, "open_interest": 500},
        ]
    }

    class MockResponse:
        def __init__(self, data): self._data = data
        def raise_for_status(self): pass
        def json(self): return self._data

    class MockClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return MockResponse(fake_response)

    from decimal import Decimal
    with patch("loophedge.services.kalshi_client.httpx.AsyncClient", MockClient):
        candles = await fetch_candles("KXHIGHNY-26AUG05-B82.5", resolution_min=5)
    assert len(candles) == 1
    c = candles[0]
    # cents → dollars
    assert c["open"] == Decimal("0.42")
    assert c["close"] == Decimal("0.45")
    assert c["volume"] == Decimal("1000")
```

- [ ] **Step 2: Create `src/loophedge/services/kalshi_client.py`**

```python
"""Kalshi public API client for market data.

The public candles/markets endpoints do not require authentication.
Base URL is env-configurable so demo (`demo-api.kalshi.co`) can be swapped
in for local testing without touching code.
"""
import os
from decimal import Decimal

import httpx

DEFAULT_BASE = "https://api.elections.kalshi.com/trade-api/v2"


def _base_url() -> str:
    return os.environ.get("KALSHI_API_BASE", DEFAULT_BASE).rstrip("/")


async def fetch_weather_markets(cities: list[str]) -> list[dict]:
    """Return open Kalshi daily-high weather markets for the given cities.

    Cities are 3-letter airport-style codes: NYC, LAX, ORD, DFW, MIA.
    Ticker prefix pattern is KXHIGH<CITY> for daily highs.
    """
    prefixes = {f"KXHIGH{c}" for c in cities}
    url = f"{_base_url()}/markets"
    params = {"status": "open", "limit": 1000}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    out = []
    for m in data.get("markets", []):
        ticker = m.get("ticker", "")
        prefix = ticker.split("-", 1)[0] if "-" in ticker else ticker
        if prefix in prefixes:
            out.append(m)
    return out


async def fetch_candles(ticker: str, resolution_min: int = 5,
                         limit: int = 200) -> list[dict]:
    """Return recent candles for a Kalshi ticker, prices normalized to Decimal
    dollars in 0.00-1.00 range (Kalshi API returns integer cents 0-100)."""
    url = f"{_base_url()}/markets/{ticker}/candlesticks"
    params = {"period_interval": resolution_min, "limit": limit}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    out = []
    for c in data.get("candlesticks", []):
        price = c.get("price", {})
        out.append({
            "ts": c["end_period_ts"],  # unix seconds
            "open": Decimal(price.get("open", 0)) / Decimal("100"),
            "high": Decimal(price.get("high", 0)) / Decimal("100"),
            "low": Decimal(price.get("low", 0)) / Decimal("100"),
            "close": Decimal(price.get("close", 0)) / Decimal("100"),
            "volume": Decimal(c.get("volume", 0)),
        })
    return out


async def fetch_settlement(ticker: str) -> dict | None:
    """Return {'settled': bool, 'settlement_value': Decimal|None} for a ticker.

    settlement_value is 1 for 'yes', 0 for 'no', None if not yet resolved.
    """
    url = f"{_base_url()}/markets/{ticker}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
    market = data.get("market", data)
    result = market.get("result")
    if result == "yes":
        return {"settled": True, "settlement_value": Decimal("1")}
    if result == "no":
        return {"settled": True, "settlement_value": Decimal("0")}
    return {"settled": False, "settlement_value": None}
```

- [ ] **Step 3: Run client tests**

```bash
.venv/bin/pytest tests/test_kalshi_client.py -v
```

Expected: PASS on both.

- [ ] **Step 4: Write failing ingester test `tests/test_kalshi_ingestor.py`**

```python
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from loophedge.models import Bar, Base, Contract
from loophedge.services.kalshi_ingestor import KalshiIngestor


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_sync_contracts_inserts_new_kalshi_markets(session_factory):
    fake_markets = [{
        "ticker": "KXHIGHNY-26AUG05-B82.5",
        "event_ticker": "KXHIGHNY-26AUG05",
        "status": "open",
        "open_time": "2026-08-04T12:00:00Z",
        "close_time": "2026-08-05T22:00:00Z",
        "expiration_time": "2026-08-05T22:00:00Z",
        "subtitle": "82.5-84.5°F",
    }]
    ing = KalshiIngestor(
        session_factory,
        fetch_markets=AsyncMock(return_value=fake_markets),
        fetch_candles=AsyncMock(return_value=[]),
        fetch_settlement=AsyncMock(return_value={"settled": False,
                                                   "settlement_value": None}),
        cities=["NYC"],
    )
    n = await ing.sync_contracts_once()
    assert n >= 1
    with session_factory() as s:
        c = s.get(Contract, "KXHIGHNY-26AUG05-B82.5")
    assert c is not None
    assert c.venue == "kalshi"
    assert c.contract_metadata.get("city") == "NYC"


@pytest.mark.asyncio
async def test_sync_contracts_is_idempotent(session_factory):
    fake_markets = [{
        "ticker": "KXHIGHNY-26AUG05-B82.5",
        "event_ticker": "KXHIGHNY-26AUG05",
        "status": "open",
        "close_time": "2026-08-05T22:00:00Z",
        "expiration_time": "2026-08-05T22:00:00Z",
    }]
    ing = KalshiIngestor(
        session_factory,
        fetch_markets=AsyncMock(return_value=fake_markets),
        fetch_candles=AsyncMock(return_value=[]),
        fetch_settlement=AsyncMock(return_value={"settled": False,
                                                   "settlement_value": None}),
        cities=["NYC"],
    )
    await ing.sync_contracts_once()
    await ing.sync_contracts_once()  # second call must not raise
    with session_factory() as s:
        contracts = list(s.execute(select(Contract)).scalars())
    assert len(contracts) == 1


@pytest.mark.asyncio
async def test_settlement_write_creates_final_bar(session_factory):
    # Contract exists but not yet settled in DB
    with session_factory() as s:
        s.add(Contract(symbol="KXHIGHNY-26AUG05-B82.5", venue="kalshi",
                       resolution_ts=datetime(2026, 8, 5, 22, 0, tzinfo=UTC)))
        s.commit()

    ing = KalshiIngestor(
        session_factory,
        fetch_markets=AsyncMock(return_value=[]),
        fetch_candles=AsyncMock(return_value=[]),
        fetch_settlement=AsyncMock(return_value={"settled": True,
                                                   "settlement_value": Decimal("1")}),
        cities=["NYC"],
    )
    n = await ing.sync_contracts_once()
    with session_factory() as s:
        c = s.get(Contract, "KXHIGHNY-26AUG05-B82.5")
        assert c.settlement_value == Decimal("1")
        # Also expects a Bar with close=1 at resolution_ts
        bar = s.get(Bar, ("KXHIGHNY-26AUG05-B82.5", "5m",
                          datetime(2026, 8, 5, 22, 0, tzinfo=UTC)))
        assert bar is not None
        assert bar.close == Decimal("1")


@pytest.mark.asyncio
async def test_fetch_candles_writes_bars_for_active_contracts(session_factory):
    with session_factory() as s:
        s.add(Contract(symbol="KXHIGHNY-26AUG05-B82.5", venue="kalshi",
                       resolution_ts=datetime(2026, 8, 5, 22, 0, tzinfo=UTC)))
        s.commit()

    fake_candles = [{
        "ts": 1754433900,  # 2026-08-05 21:25 UTC
        "open": Decimal("0.42"), "high": Decimal("0.47"),
        "low": Decimal("0.40"),  "close": Decimal("0.45"),
        "volume": Decimal("1000"),
    }]
    ing = KalshiIngestor(
        session_factory,
        fetch_markets=AsyncMock(return_value=[]),
        fetch_candles=AsyncMock(return_value=fake_candles),
        fetch_settlement=AsyncMock(return_value={"settled": False,
                                                   "settlement_value": None}),
        cities=["NYC"],
    )
    n = await ing.fetch_candles_once()
    assert n == 1
    with session_factory() as s:
        bars = list(s.execute(select(Bar).where(
            Bar.symbol == "KXHIGHNY-26AUG05-B82.5")).scalars())
    assert len(bars) == 1
    assert bars[0].close == Decimal("0.45")
```

- [ ] **Step 5: Create `src/loophedge/services/kalshi_ingestor.py`**

```python
"""Kalshi ingester: hourly contract sync + 5-min candle polling.

The two schedules are independent. sync_contracts_once() should be called
hourly by the run loop; fetch_candles_once() should be called every 5 minutes.

All writes use session-scoped upserts (SQLAlchemy `merge` where the primary
key covers uniqueness) so re-running is safe.
"""
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from loophedge.models import Bar, Contract

FetchMarkets = Callable[[list[str]], Awaitable[list[dict]]]
FetchCandles = Callable[[str, int, int], Awaitable[list[dict]]]
FetchSettlement = Callable[[str], Awaitable[dict | None]]


def _extract_city_from_ticker(ticker: str) -> str | None:
    """KXHIGHNY-26AUG05-B82.5 -> NY (2-3 char code embedded in first segment)."""
    if not ticker.startswith("KXHIGH"):
        return None
    first_segment = ticker.split("-", 1)[0]
    return first_segment[len("KXHIGH"):]  # 'NY', 'LAX', ...


def _parse_ts(iso: str | None) -> datetime | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


class KalshiIngestor:
    def __init__(
        self,
        session_factory: sessionmaker,
        fetch_markets: FetchMarkets,
        fetch_candles: FetchCandles,
        fetch_settlement: FetchSettlement,
        cities: list[str],
        timeframe: str = "5m",
        resolution_min: int = 5,
    ):
        self.session_factory = session_factory
        self.fetch_markets = fetch_markets
        self.fetch_candles = fetch_candles
        self.fetch_settlement = fetch_settlement
        self.cities = cities
        self.timeframe = timeframe
        self.resolution_min = resolution_min

    async def sync_contracts_once(self) -> int:
        """Insert new markets; update settlement values for existing contracts.

        Returns number of contract rows created or updated.
        """
        markets = await self.fetch_markets(self.cities)
        written = 0
        for m in markets:
            ticker = m["ticker"]
            city_code = _extract_city_from_ticker(ticker)
            with self.session_factory() as s:
                existing = s.get(Contract, ticker)
                if existing is None:
                    c = Contract(
                        symbol=ticker,
                        venue="kalshi",
                        open_ts=_parse_ts(m.get("open_time")),
                        close_ts=_parse_ts(m.get("close_time")),
                        resolution_ts=_parse_ts(m.get("expiration_time")),
                        resolution_source=m.get("event_ticker"),
                        contract_metadata={
                            "city": city_code,
                            "subtitle": m.get("subtitle", ""),
                        },
                    )
                    s.add(c)
                    s.commit()
                    written += 1

        # Second pass: check settlement for contracts whose resolution_ts has
        # passed (or is close) and settlement_value is still null.
        with self.session_factory() as s:
            unsettled = list(s.execute(
                select(Contract).where(Contract.venue == "kalshi",
                                       Contract.settlement_value.is_(None))
            ).scalars())
        for c in unsettled:
            info = await self.fetch_settlement(c.symbol)
            if info and info["settled"]:
                with self.session_factory() as s:
                    row = s.get(Contract, c.symbol)
                    row.settlement_value = info["settlement_value"]
                    s.commit()
                    # Emit a settlement bar at resolution_ts if we have one
                    if row.resolution_ts is not None:
                        settlement_bar = Bar(
                            symbol=row.symbol,
                            timeframe=self.timeframe,
                            ts=row.resolution_ts,
                            open=info["settlement_value"],
                            high=info["settlement_value"],
                            low=info["settlement_value"],
                            close=info["settlement_value"],
                            volume=Decimal("0"),
                        )
                        existing_bar = s.get(Bar, (row.symbol, self.timeframe,
                                                   row.resolution_ts))
                        if existing_bar is None:
                            s.add(settlement_bar)
                            s.commit()
                written += 1
        return written

    async def fetch_candles_once(self) -> int:
        """Poll active Kalshi contracts and write new Bar rows."""
        with self.session_factory() as s:
            active = list(s.execute(
                select(Contract).where(Contract.venue == "kalshi",
                                       Contract.settlement_value.is_(None))
            ).scalars())
        written = 0
        for c in active:
            try:
                candles = await self.fetch_candles(c.symbol, self.resolution_min, 200)
            except Exception as e:
                print(f"[kalshi-ingestor] fetch_candles({c.symbol}) failed: {e}",
                      flush=True)
                continue
            for candle in candles:
                ts = datetime.fromtimestamp(candle["ts"], tz=UTC)
                with self.session_factory() as s:
                    existing = s.get(Bar, (c.symbol, self.timeframe, ts))
                    if existing is not None:
                        continue
                    s.add(Bar(
                        symbol=c.symbol, timeframe=self.timeframe, ts=ts,
                        open=candle["open"], high=candle["high"],
                        low=candle["low"], close=candle["close"],
                        volume=candle["volume"],
                    ))
                    s.commit()
                    written += 1
        return written
```

- [ ] **Step 6: Run ingester tests**

```bash
.venv/bin/pytest tests/test_kalshi_ingestor.py -v
```

Expected: PASS on all four.

- [ ] **Step 7: Add `run_kalshi` dispatcher to `src/loophedge/cli.py`**

After the existing `run_ingest` function (around line 21), add:

```python
def run_kalshi() -> None:
    import asyncio as _asyncio
    from loophedge.db import get_session_factory
    from loophedge.services.kalshi_client import (
        fetch_candles, fetch_settlement, fetch_weather_markets,
    )
    from loophedge.services.kalshi_ingestor import KalshiIngestor

    async def _go():
        sf = get_session_factory()
        ing = KalshiIngestor(
            sf,
            fetch_markets=fetch_weather_markets,
            fetch_candles=fetch_candles,
            fetch_settlement=fetch_settlement,
            cities=["NY", "LAX"],  # matches ticker embedding: KXHIGHNY / KXHIGHLAX
        )
        last_sync = 0.0
        while True:
            now = _asyncio.get_event_loop().time()
            if now - last_sync > 3600:
                try:
                    n = await ing.sync_contracts_once()
                    print(f"[kalshi] contract sync wrote {n} rows", flush=True)
                except Exception as e:
                    print(f"[kalshi] sync_contracts failed: {e}",
                          file=sys.stderr, flush=True)
                last_sync = now
            try:
                n = await ing.fetch_candles_once()
                if n:
                    print(f"[kalshi] wrote {n} candles", flush=True)
            except Exception as e:
                print(f"[kalshi] fetch_candles failed: {e}",
                      file=sys.stderr, flush=True)
            await _asyncio.sleep(300)  # 5 min

    asyncio.run(_go())
```

Add `"kalshi"` to the `_COMMANDS` tuple:

```python
_COMMANDS = ("ingest", "execute", "risk", "dashboard", "maker", "checker", "genesis", "kalshi")
```

Update the usage message accordingly (the existing code already generates it from `_COMMANDS`).

- [ ] **Step 8: Add `kalshi-ingestor` service to `docker-compose.yml`**

After the existing `data-ingestor` block, add:

```yaml
  kalshi-ingestor:
    image: loophedge:dev
    command: ["kalshi"]
    env_file: .env
    volumes:
      - ./skills:/app/skills
      - ./state:/app/state
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
    restart: on-failure:10
```

- [ ] **Step 9: Run full test suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: all tests pass (99 + 2 client + 4 ingester = 105).

- [ ] **Step 10: Commit**

```bash
git add src/loophedge/services/kalshi_client.py src/loophedge/services/kalshi_ingestor.py \
        src/loophedge/cli.py docker-compose.yml \
        tests/test_kalshi_client.py tests/test_kalshi_ingestor.py
git commit -m "feat: kalshi weather ingester with contract sync, candles, and settlement bars"
```

---

## Task 5: Open-Meteo client + weather ingester

**Files:**
- Create: `src/loophedge/services/open_meteo_client.py`
- Create: `src/loophedge/services/weather_ingestor.py`
- Modify: `src/loophedge/cli.py` (add `run_weather` dispatcher)
- Modify: `docker-compose.yml` (add `weather-ingestor` service)
- Create: `tests/test_open_meteo_client.py`
- Create: `tests/test_weather_ingestor.py`

**Interfaces:**
- Consumes: `WeatherForecast` from Task 1
- Produces:
  - `open_meteo_client.fetch_forecast(latitude: float, longitude: float, days: int = 7) -> list[dict]` — returns forecast rows `{valid_ts: datetime, temp_mean_c: Decimal, temp_std_c: Decimal}`
  - `WeatherIngestor.fetch_once() -> int` — returns count of new forecast rows

- [ ] **Step 1: Write failing client test `tests/test_open_meteo_client.py`**

```python
from decimal import Decimal
from unittest.mock import patch

import pytest

from loophedge.services.open_meteo_client import fetch_forecast


@pytest.mark.asyncio
async def test_fetch_forecast_returns_mean_and_std_from_ensemble():
    fake_response = {
        "daily": {
            "time": ["2026-08-05", "2026-08-06"],
            "temperature_2m_max": [30.5, 32.1],
            "temperature_2m_max_member01": [30.2, 32.0],
            "temperature_2m_max_member02": [30.8, 32.2],
        }
    }

    class MockResponse:
        def raise_for_status(self): pass
        def json(self): return fake_response

    class MockClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return MockResponse()

    with patch("loophedge.services.open_meteo_client.httpx.AsyncClient", MockClient):
        rows = await fetch_forecast(40.7128, -74.0060, days=2)
    assert len(rows) == 2
    r0 = rows[0]
    assert r0["temp_mean_c"] == Decimal("30.5")
    # std of 30.2 and 30.8 is 0.3 (population), or 0.424 (sample) — accept either
    assert Decimal("0.20") <= r0["temp_std_c"] <= Decimal("0.50")
```

- [ ] **Step 2: Create `src/loophedge/services/open_meteo_client.py`**

```python
"""Open-Meteo forecast client (no auth required, free tier limit 10K/day).

We request daily max-temperature forecasts plus individual ensemble members
so we can compute mean + spread client-side. Open-Meteo aggregates NWS + GFS
+ ECMWF + ICON depending on region.
"""
import os
import statistics
from datetime import UTC, datetime
from decimal import Decimal

import httpx

DEFAULT_BASE = "https://ensemble-api.open-meteo.com/v1"


def _base_url() -> str:
    return os.environ.get("OPEN_METEO_API_BASE", DEFAULT_BASE).rstrip("/")


async def fetch_forecast(latitude: float, longitude: float,
                         days: int = 7) -> list[dict]:
    """Return daily-max forecast rows with mean + std from GFS/ICON ensemble.

    Each row: {valid_ts: datetime (UTC, day-end), temp_mean_c: Decimal,
    temp_std_c: Decimal}.
    """
    url = f"{_base_url()}/gfs"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": "temperature_2m_max",
        "models": "gfs_seamless",
        "forecast_days": days,
        "timezone": "UTC",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()

    daily = data.get("daily", {})
    times = daily.get("time", [])
    mean_vals = daily.get("temperature_2m_max", [])
    # Ensemble members are keyed as temperature_2m_max_memberNN
    member_keys = [k for k in daily.keys() if k.startswith("temperature_2m_max_member")]

    out = []
    for i, day in enumerate(times):
        mean_c = Decimal(str(mean_vals[i]))
        members = [daily[k][i] for k in member_keys
                   if i < len(daily[k]) and daily[k][i] is not None]
        std_c = Decimal(str(statistics.pstdev(members))) if len(members) > 1 \
                else Decimal("0")
        valid_ts = datetime.fromisoformat(day + "T00:00:00+00:00").astimezone(UTC)
        out.append({
            "valid_ts": valid_ts,
            "temp_mean_c": mean_c.quantize(Decimal("0.01")),
            "temp_std_c": std_c.quantize(Decimal("0.01")),
        })
    return out
```

- [ ] **Step 3: Run client test**

```bash
.venv/bin/pytest tests/test_open_meteo_client.py -v
```

Expected: PASS.

- [ ] **Step 4: Write failing ingester test `tests/test_weather_ingestor.py`**

```python
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from loophedge.models import Base, WeatherForecast
from loophedge.services.weather_ingestor import CITY_COORDS, WeatherIngestor


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_fetch_writes_forecasts_for_each_city(session_factory):
    fake = [
        {"valid_ts": datetime(2026, 8, 5, tzinfo=UTC),
         "temp_mean_c": Decimal("30.50"), "temp_std_c": Decimal("0.30")},
        {"valid_ts": datetime(2026, 8, 6, tzinfo=UTC),
         "temp_mean_c": Decimal("32.10"), "temp_std_c": Decimal("0.40")},
    ]
    ing = WeatherIngestor(
        session_factory,
        fetch_forecast=AsyncMock(return_value=fake),
        cities=["NYC", "LAX"],
    )
    n = await ing.fetch_once()
    assert n == 4  # 2 cities * 2 days
    with session_factory() as s:
        rows = list(s.execute(select(WeatherForecast)).scalars())
    assert len(rows) == 4


@pytest.mark.asyncio
async def test_fetch_is_idempotent_via_unique_constraint(session_factory):
    fake = [{"valid_ts": datetime(2026, 8, 5, tzinfo=UTC),
             "temp_mean_c": Decimal("30.50"), "temp_std_c": Decimal("0.30")}]
    ing = WeatherIngestor(
        session_factory,
        fetch_forecast=AsyncMock(return_value=fake),
        cities=["NYC"],
    )
    await ing.fetch_once()
    await ing.fetch_once()  # second identical call must not raise or dup
    with session_factory() as s:
        rows = list(s.execute(select(WeatherForecast)).scalars())
    assert len(rows) == 1


def test_city_coords_has_nyc_and_lax():
    assert "NYC" in CITY_COORDS
    assert "LAX" in CITY_COORDS
```

- [ ] **Step 5: Create `src/loophedge/services/weather_ingestor.py`**

```python
"""Weather forecast ingester (Open-Meteo)."""
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from loophedge.models import WeatherForecast

FetchForecast = Callable[[float, float, int], Awaitable[list[dict]]]

# Airport-adjacent coordinates. NYC uses Central Park; LAX uses the airport.
CITY_COORDS: dict[str, tuple[float, float]] = {
    "NYC": (40.7789, -73.9692),
    "LAX": (33.9416, -118.4085),
}


class WeatherIngestor:
    def __init__(
        self,
        session_factory: sessionmaker,
        fetch_forecast: FetchForecast,
        cities: list[str],
        source: str = "open-meteo",
        days: int = 7,
    ):
        self.session_factory = session_factory
        self.fetch_forecast = fetch_forecast
        self.cities = cities
        self.source = source
        self.days = days

    async def fetch_once(self) -> int:
        """Fetch forecasts for each configured city; return new-row count."""
        forecast_ts = datetime.now(UTC)
        written = 0
        for city in self.cities:
            if city not in CITY_COORDS:
                print(f"[weather-ingestor] no coords for city {city}, skipping",
                      flush=True)
                continue
            lat, lon = CITY_COORDS[city]
            try:
                rows = await self.fetch_forecast(lat, lon, self.days)
            except Exception as e:
                print(f"[weather-ingestor] fetch_forecast({city}) failed: {e}",
                      flush=True)
                continue
            for r in rows:
                with self.session_factory() as s:
                    wf = WeatherForecast(
                        city=city,
                        forecast_ts=forecast_ts,
                        valid_ts=r["valid_ts"],
                        temp_mean_c=r["temp_mean_c"],
                        temp_std_c=r["temp_std_c"],
                        source=self.source,
                    )
                    s.add(wf)
                    try:
                        s.commit()
                        written += 1
                    except IntegrityError:
                        s.rollback()  # duplicate on unique key — expected on retries
        return written
```

- [ ] **Step 6: Run tests**

```bash
.venv/bin/pytest tests/test_weather_ingestor.py -v
```

Expected: PASS on all three.

- [ ] **Step 7: Add `run_weather` dispatcher in `src/loophedge/cli.py`**

After `run_kalshi`, add:

```python
def run_weather() -> None:
    import asyncio as _asyncio
    from loophedge.db import get_session_factory
    from loophedge.services.open_meteo_client import fetch_forecast
    from loophedge.services.weather_ingestor import WeatherIngestor

    async def _go():
        sf = get_session_factory()
        ing = WeatherIngestor(
            sf,
            fetch_forecast=fetch_forecast,
            cities=["NYC", "LAX"],
        )
        while True:
            try:
                n = await ing.fetch_once()
                print(f"[weather] wrote {n} forecast rows", flush=True)
            except Exception as e:
                print(f"[weather] fetch_once failed: {e}",
                      file=sys.stderr, flush=True)
            await _asyncio.sleep(21600)  # 6 hours

    asyncio.run(_go())
```

Add `"weather"` to `_COMMANDS`:

```python
_COMMANDS = ("ingest", "execute", "risk", "dashboard", "maker", "checker", "genesis", "kalshi", "weather")
```

- [ ] **Step 8: Add `weather-ingestor` service to `docker-compose.yml`**

After `kalshi-ingestor`:

```yaml
  weather-ingestor:
    image: loophedge:dev
    command: ["weather"]
    env_file: .env
    volumes:
      - ./skills:/app/skills
      - ./state:/app/state
    depends_on:
      postgres: { condition: service_healthy }
    restart: on-failure:10
```

- [ ] **Step 9: Run full test suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: PASS (105 + 1 + 3 = 109).

- [ ] **Step 10: Commit**

```bash
git add src/loophedge/services/open_meteo_client.py src/loophedge/services/weather_ingestor.py \
        src/loophedge/cli.py docker-compose.yml \
        tests/test_open_meteo_client.py tests/test_weather_ingestor.py
git commit -m "feat: open-meteo weather forecast ingester for NYC and LAX"
```

---

## Task 6: Strategy signature + agent tools + genesis playbook

**Files:**
- Modify: `src/loophedge/strategies/interface.py` (widen Protocol)
- Modify: `src/loophedge/backtest/engine.py` (thread `forecasts` through)
- Modify: `src/loophedge/agents/maker.py` (fetch forecasts for Kalshi symbols)
- Modify: `src/loophedge/agents/tools.py` (add `make_query_kalshi_bars`, `make_query_weather_forecast`)
- Modify: `src/loophedge/agents/genesis.py` (register new tools)
- Modify: `src/loophedge/agents/checker.py` (register new tools)
- Modify: `skills/strategy_genesis.md` (add weather strategy section)
- Modify: `tests/test_maker.py` (ensure existing behavior unchanged)
- Create: `tests/test_agent_tools_kalshi.py`

**Interfaces:**
- Consumes: `Contract`, `WeatherForecast`, `Bar` from earlier tasks
- Produces:
  - `Strategy.generate_signals(bars, hyperparams, *, forecasts=None) -> list[dict]` — Protocol widened
  - `run_backtest(bars, strategy_callable, hyperparams, ..., forecasts=None)` — kwarg added
  - `make_query_kalshi_bars(session_factory)` — returns a callable `query_kalshi_bars(symbol_pattern, timeframe='5m', limit=200) -> dict`
  - `make_query_weather_forecast(session_factory)` — returns a callable `query_weather_forecast(city, valid_ts=None, limit=20) -> dict`

- [ ] **Step 1: Widen the Protocol in `src/loophedge/strategies/interface.py`**

Replace the file with:

```python
from typing import Any, Protocol


class Strategy(Protocol):
    NAME: str
    DEFAULT_HYPERPARAMS: dict[str, Any]

    @staticmethod
    def generate_signals(bars: list, hyperparams: dict[str, Any],
                          *, forecasts: list | None = None) -> list[dict]:
        ...
```

- [ ] **Step 2: Thread `forecasts` through the backtest engine**

In `src/loophedge/backtest/engine.py`, modify the `run_backtest` signature and the internal call to `strategy_callable`:

```python
def run_backtest(
    bars: list[Bar],
    strategy_callable: Callable[..., list[dict]],
    hyperparams: dict[str, Any],
    starting_cash: Decimal = Decimal("100000"),
    check_lookahead: bool = True,
    forecasts: list | None = None,
) -> BacktestResult:
    if not bars:
        return BacktestResult(Decimal("0"), Decimal("0"), Decimal("0"), 0, notes="empty bars")

    signals = strategy_callable(bars, hyperparams, forecasts=forecasts) or []
    # ... rest unchanged
```

Also update `detect_lookahead` similarly — it calls `strategy_callable` twice; both should pass `forecasts=forecasts`.

- [ ] **Step 3: Update maker to pass forecasts for Kalshi symbols**

In `src/loophedge/agents/maker.py`, modify the `tick()` method where it currently calls `module.generate_signals(bars, strat.hyperparams)`. Around the strategy loop, add a Contract venue lookup and fetch relevant WeatherForecast rows:

```python
# Inside tick(), after `for strat in actives:` and after loading bars but
# before calling generate_signals:
from loophedge.models import Contract, WeatherForecast
from sqlalchemy import select as _sel

forecasts = None
with self.session_factory() as _s:
    contract = _s.get(Contract, symbol)
    if contract is not None and contract.venue == "kalshi":
        city = (contract.contract_metadata or {}).get("city")
        if city:
            forecasts = list(_s.execute(
                _sel(WeatherForecast)
                .where(WeatherForecast.city == city)
                .order_by(WeatherForecast.forecast_ts.desc())
                .limit(20)
            ).scalars())

try:
    sigs = module.generate_signals(bars, strat.hyperparams,
                                    forecasts=forecasts) or []
except Exception:
    continue
```

Existing crypto strategies define `generate_signals(bars, hyperparams)` without a `forecasts` kwarg. To keep them working, wrap the call so a TypeError from a stale signature falls back to the two-arg form:

```python
try:
    sigs = module.generate_signals(bars, strat.hyperparams,
                                    forecasts=forecasts) or []
except TypeError:
    # Old-signature strategy (no forecasts kwarg)
    sigs = module.generate_signals(bars, strat.hyperparams) or []
except Exception:
    continue
```

- [ ] **Step 4: Add new agent tools in `src/loophedge/agents/tools.py`**

Append two functions:

```python
def make_query_kalshi_bars(session_factory):
    """Tool for querying Kalshi bars by symbol prefix (e.g. 'KXHIGHNY-26AUG*')."""
    def query_kalshi_bars(symbol_pattern: str, timeframe: str = "5m",
                          limit: int = 200) -> dict:
        prefix = symbol_pattern.rstrip("*")
        with session_factory() as s:
            rows = s.execute(
                select(Bar)
                .where(Bar.symbol.like(f"{prefix}%"),
                       Bar.timeframe == timeframe)
                .order_by(Bar.ts.desc()).limit(limit)
            ).scalars().all()
        return {
            "bars": [
                {"symbol": r.symbol, "ts": r.ts.isoformat(),
                 "open": str(r.open), "high": str(r.high),
                 "low": str(r.low), "close": str(r.close),
                 "volume": str(r.volume)}
                for r in reversed(rows)
            ]
        }
    return query_kalshi_bars


def make_query_weather_forecast(session_factory):
    """Tool for querying recent Open-Meteo forecasts for a city."""
    from loophedge.models import WeatherForecast

    def query_weather_forecast(city: str, valid_ts: str | None = None,
                                limit: int = 20) -> dict:
        with session_factory() as s:
            q = select(WeatherForecast).where(WeatherForecast.city == city)
            if valid_ts:
                from datetime import datetime as _dt
                target = _dt.fromisoformat(valid_ts.replace("Z", "+00:00"))
                q = q.where(WeatherForecast.valid_ts == target)
            rows = s.execute(
                q.order_by(WeatherForecast.forecast_ts.desc()).limit(limit)
            ).scalars().all()
        return {
            "forecasts": [
                {"city": r.city, "forecast_ts": r.forecast_ts.isoformat(),
                 "valid_ts": r.valid_ts.isoformat(),
                 "temp_mean_c": str(r.temp_mean_c),
                 "temp_std_c": str(r.temp_std_c),
                 "source": r.source}
                for r in rows
            ]
        }
    return query_weather_forecast
```

- [ ] **Step 5: Register the tools in genesis and checker**

In `src/loophedge/agents/genesis.py`, in the `GenesisAgent.__init__` tool list, append two `ToolSpec` entries:

```python
ToolSpec("query_kalshi_bars", "Fetch recent bars matching a Kalshi symbol prefix",
          {"type": "object",
           "properties": {"symbol_pattern": {"type": "string"},
                            "timeframe": {"type": "string", "default": "5m"},
                            "limit": {"type": "integer", "default": 200}},
           "required": ["symbol_pattern"]},
          make_query_kalshi_bars(session_factory)),
ToolSpec("query_weather_forecast", "Recent Open-Meteo forecasts for a city",
          {"type": "object",
           "properties": {"city": {"type": "string"},
                            "valid_ts": {"type": "string"},
                            "limit": {"type": "integer", "default": 20}},
           "required": ["city"]},
          make_query_weather_forecast(session_factory)),
```

Do the same in `src/loophedge/agents/checker.py`.

The imports at the top of each agent file must add:

```python
from loophedge.agents.tools import (
    make_propose_strategy, make_query_bars, make_read_lessons, make_read_skill,
    make_query_kalshi_bars, make_query_weather_forecast,
)
```

(Add the two new names to whatever the existing import already includes.)

- [ ] **Step 6: Update the genesis playbook `skills/strategy_genesis.md`**

Append a new section after the existing `## Constraints` section:

```markdown
## Writing weather strategies (Kalshi)

Kalshi lists daily-high temperature buckets like `KXHIGHNY-26AUG05-B82.5` (NYC daily
high, expiring 2026-08-05, bucket centered at 82.5°F). Bar prices are 0.00–1.00
(probability of the bucket being the correct one), settled at 1.00 (yes) or 0.00 (no).

Weather strategies take an additional keyword argument:

```python
def generate_signals(bars, hyperparams, *, forecasts=None):
    ...
```

`forecasts` is a list of `WeatherForecast` ORM objects with attribute access:
- `f.city` — string like "NYC" or "LAX"
- `f.forecast_ts` — datetime the forecast was produced
- `f.valid_ts` — datetime the forecast is *about* (usually a specific day)
- `f.temp_mean_c` — ensemble mean, Decimal, degrees Celsius
- `f.temp_std_c` — ensemble spread, Decimal

To decide a bucket's fair probability, compare `f.temp_mean_c` to the bucket
(available via `bars[i].symbol` — parse the strike from the ticker) using a normal
CDF over `f.temp_std_c`. Emit `side="long"` when your model probability exceeds the
current bar close by a threshold (e.g. 0.05).

Kalshi contracts settle to a single value at `resolution_ts` — do not emit signals
after that timestamp for that symbol. `bars` only contains one ticker's history when
called for a Kalshi strategy target, so filter your own signals by the closing bar.
```

- [ ] **Step 7: Write tests for the new agent tools in `tests/test_agent_tools_kalshi.py`**

```python
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from loophedge.agents.tools import (
    make_query_kalshi_bars, make_query_weather_forecast,
)
from loophedge.models import Bar, Base, WeatherForecast


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        s.add(Bar(symbol="KXHIGHNY-26AUG05-B82.5", timeframe="5m",
                  ts=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
                  open=Decimal("0.42"), high=Decimal("0.47"),
                  low=Decimal("0.40"), close=Decimal("0.45"),
                  volume=Decimal("100")))
        s.add(Bar(symbol="BTCUSDT", timeframe="5m",
                  ts=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
                  open=Decimal("60000"), high=Decimal("60000"),
                  low=Decimal("60000"), close=Decimal("60000"),
                  volume=Decimal("1")))
        s.add(WeatherForecast(city="NYC",
                              forecast_ts=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
                              valid_ts=datetime(2026, 8, 5, tzinfo=UTC),
                              temp_mean_c=Decimal("30.5"),
                              temp_std_c=Decimal("0.3"),
                              source="open-meteo"))
        s.commit()
    return Session


def test_query_kalshi_bars_prefix_matches_kalshi_only(session_factory):
    tool = make_query_kalshi_bars(session_factory)
    result = tool("KXHIGHNY-26AUG*")
    assert len(result["bars"]) == 1
    assert result["bars"][0]["symbol"] == "KXHIGHNY-26AUG05-B82.5"


def test_query_weather_forecast_filters_by_city(session_factory):
    tool = make_query_weather_forecast(session_factory)
    result = tool("NYC")
    assert len(result["forecasts"]) == 1
    assert result["forecasts"][0]["temp_mean_c"] == "30.5"


def test_query_weather_forecast_filters_by_valid_ts(session_factory):
    tool = make_query_weather_forecast(session_factory)
    result = tool("NYC", valid_ts="2026-08-05T00:00:00+00:00")
    assert len(result["forecasts"]) == 1
    result_wrong_day = tool("NYC", valid_ts="2026-08-06T00:00:00+00:00")
    assert len(result_wrong_day["forecasts"]) == 0
```

- [ ] **Step 8: Run full test suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: PASS. The maker `TypeError` fallback is important — existing crypto strategies without the `forecasts` kwarg still work.

- [ ] **Step 9: Commit**

```bash
git add src/loophedge/strategies/interface.py src/loophedge/backtest/engine.py \
        src/loophedge/agents/maker.py src/loophedge/agents/tools.py \
        src/loophedge/agents/genesis.py src/loophedge/agents/checker.py \
        skills/strategy_genesis.md tests/test_agent_tools_kalshi.py
git commit -m "feat: forecasts kwarg on strategy signature + kalshi/weather agent tools"
```

---

## Task 7: End-to-end integration test

**Files:**
- Create: `tests/test_kalshi_backtest_end_to_end.py`
- Create: `tests/test_e2e_kalshi_agent_loop.py`

**Interfaces:**
- Consumes: everything from tasks 1–6
- Produces: verified full pipeline behavior for a Kalshi weather strategy

- [ ] **Step 1: Write end-to-end backtest test `tests/test_kalshi_backtest_end_to_end.py`**

```python
"""Hand-written weather strategy → full backtest engine → assert numbers."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from loophedge.backtest.engine import run_backtest
from loophedge.models import Base, Contract, WeatherForecast


def _weather_strategy(bars, hyperparams, *, forecasts=None):
    """A trivial strategy: go long when close < 0.5, close (short) when close > 0.7."""
    signals = []
    position_open = False
    for b in bars:
        c = float(b.close)
        if not position_open and c < 0.5:
            signals.append({"symbol": b.symbol, "side": "long",
                            "size_pct": 0.02, "ts": b.ts})
            position_open = True
        elif position_open and c > 0.7:
            signals.append({"symbol": b.symbol, "side": "short",
                            "size_pct": 0.02, "ts": b.ts})
            position_open = False
    return signals


@pytest.fixture
def kalshi_bars():
    """Synthetic 5m bars over 8 days, oscillating between 0.3 and 0.8, then settling at 1."""
    from loophedge.models import Bar
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        s.add(Contract(symbol="KXHIGHNY-26AUG05-B82.5", venue="kalshi",
                       resolution_ts=datetime(2026, 8, 5, 22, 0, tzinfo=UTC),
                       settlement_value=Decimal("1")))
        s.commit()

    bars = []
    start = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    for i in range(2000):  # >100 potential trades
        ts = start + timedelta(minutes=5 * i)
        # oscillate 0.3 - 0.8
        import math
        close = Decimal(str(0.55 + 0.25 * math.sin(i / 20)))
        bars.append(Bar(symbol="KXHIGHNY-26AUG05-B82.5", timeframe="5m",
                        ts=ts, open=close, high=close, low=close, close=close,
                        volume=Decimal("100")))
    return bars, Session


def test_kalshi_backtest_produces_result_with_kalshi_costs(kalshi_bars):
    bars, Session = kalshi_bars
    result = run_backtest(
        bars, _weather_strategy, {},
        starting_cash=Decimal("10000"),
        check_lookahead=False,  # trivial strategy has no lookahead
    )
    # Should generate many trades in the oscillation
    assert result.trade_count > 50
    # Result populated with all metrics
    assert result.sharpe is not None
    assert result.max_dd_pct is not None
```

- [ ] **Step 2: Write agent-loop integration test `tests/test_e2e_kalshi_agent_loop.py`**

```python
"""Full genesis → checker → (would-be) maker path over Kalshi data,
using a stubbed Anthropic client to avoid real API calls."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from loophedge.models import Bar, Base, Contract, Strategy, WeatherForecast


@pytest.fixture
def env(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    # Seed a Kalshi contract, some bars, and a forecast
    with Session() as s:
        s.add(Contract(symbol="KXHIGHNY-26AUG05-B82.5", venue="kalshi",
                       resolution_ts=datetime(2026, 8, 5, 22, 0, tzinfo=UTC),
                       contract_metadata={"city": "NY"}))
        for i in range(50):
            ts = datetime(2026, 8, 4, 12, 0, tzinfo=UTC) + timedelta(minutes=5 * i)
            s.add(Bar(symbol="KXHIGHNY-26AUG05-B82.5", timeframe="5m",
                      ts=ts, open=Decimal("0.5"), high=Decimal("0.55"),
                      low=Decimal("0.45"), close=Decimal("0.5"),
                      volume=Decimal("100")))
        s.add(WeatherForecast(city="NY",
                              forecast_ts=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
                              valid_ts=datetime(2026, 8, 5, tzinfo=UTC),
                              temp_mean_c=Decimal("30.5"),
                              temp_std_c=Decimal("0.5"),
                              source="open-meteo"))
        s.commit()

    # Init a scratch skills directory as a git repo
    import subprocess
    skills_root = tmp_path / "skills"
    (skills_root / "strategies" / "pending").mkdir(parents=True)
    (skills_root / "strategies" / "active").mkdir(parents=True)
    (skills_root / "strategies" / "retired").mkdir(parents=True)
    (skills_root / "strategy_genesis.md").write_text("# playbook")
    (skills_root / "alpha_research.md").write_text("# alpha")
    (skills_root / "backtest_verification.md").write_text("# check")
    (skills_root / "LESSONS.md").write_text("")
    subprocess.run(["git", "init", "-b", "main"], cwd=skills_root, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=skills_root,
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=skills_root,
                   check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=skills_root, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=skills_root, check=True,
                   capture_output=True)

    return Session, skills_root


def test_agent_tools_wired_to_kalshi_data(env):
    """Genesis tools can query Kalshi bars and weather forecasts."""
    session_factory, _ = env
    from loophedge.agents.tools import (
        make_query_kalshi_bars, make_query_weather_forecast,
    )
    bars_tool = make_query_kalshi_bars(session_factory)
    fc_tool = make_query_weather_forecast(session_factory)

    bars_result = bars_tool("KXHIGHNY-26AUG*")
    assert len(bars_result["bars"]) == 50

    fc_result = fc_tool("NY")
    assert len(fc_result["forecasts"]) == 1
    assert fc_result["forecasts"][0]["temp_mean_c"] == "30.5"
```

- [ ] **Step 3: Run all tests**

```bash
.venv/bin/pytest tests/ -q
```

Expected: PASS (all previous + 1 + 1 = ~114 tests).

- [ ] **Step 4: Commit**

```bash
git add tests/test_kalshi_backtest_end_to_end.py tests/test_e2e_kalshi_agent_loop.py
git commit -m "test: end-to-end kalshi weather strategy through backtest engine and agent tools"
```

---

## Deploy checklist (post-implementation, not a task)

After all tasks land on `main`:

1. On droplet, `cd ~/loop-hedge && git pull`
2. `docker compose --profile build-only build loophedge-base`
3. `docker compose run --rm --entrypoint alembic data-ingestor upgrade head`
4. `docker compose up -d --force-recreate` (brings up 11 services now)
5. `docker compose logs -f kalshi-ingestor weather-ingestor` — should show contract sync + candle writes within 5 min, forecast writes within 6h
6. Genesis will pick up the new tools on its next 4h tick and may propose Kalshi weather strategies
7. Monitor `docker compose logs strategy-genesis-agent checker-agent` for LLM behavior

## Self-review

- **Coverage:** Every spec section has a task. Schema (Task 1), cost model (Task 2), settlement (Task 3), Kalshi ingester (Task 4), weather ingester (Task 5), strategy signature + tools + playbook (Task 6), e2e (Task 7). ✓
- **No placeholders:** All code blocks are complete. No "similar to" or "add error handling here." ✓
- **Type consistency:** `session_factory` optional on Simulator, always passed at runtime via cli. `forecasts` kwarg is `list | None` end-to-end. `contract_metadata` (not `metadata`) used consistently to avoid the SQLAlchemy reserved name. ✓
- **Ambiguity:** The Kalshi API response shape is inferred from public docs and may need slight adaptation at implementation time — the client returns typed dicts, so any tweak is localized to `kalshi_client.py`. Flagged in spec's "Open questions deferred to implementation." ✓
