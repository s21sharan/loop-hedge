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
