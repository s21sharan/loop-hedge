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
    engine = create_engine("sqlite:///file::memory:?cache=shared&uri=true",
                           future=True,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, future=True, expire_on_commit=False)

    yield sf

    # Teardown: clear all tables for next test
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
