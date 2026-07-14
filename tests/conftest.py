from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from loophedge.models import Base


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
