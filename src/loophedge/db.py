from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from loophedge.config import get_settings


@lru_cache
def get_engine():
    return create_engine(get_settings().database_url, future=True, pool_pre_ping=True)


@lru_cache
def get_session_factory():
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def SessionLocal():
    """Backwards-compat shim: call get_session_factory() at call time."""
    return get_session_factory()()
