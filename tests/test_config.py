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
