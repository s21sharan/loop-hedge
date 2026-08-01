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
