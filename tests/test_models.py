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
