from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from loophedge.config import get_settings

_settings = get_settings()
engine = create_engine(_settings.database_url, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
