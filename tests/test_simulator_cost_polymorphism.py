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
