"""contract and weather_forecast tables

Adds Contract lifecycle table (event contracts + backfilled crypto symbols)
and WeatherForecast table for Open-Meteo forecast ingestion.

Revision ID: 004
Revises: 003
Create Date: 2026-07-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "004"
down_revision = "003"


def upgrade():
    # NOTE: migration 001 uses Base.metadata.create_all(), which now also
    # picks up the models added in later migrations (Contract, WeatherForecast).
    # The if-not-exists guards below prevent duplicate CREATE TABLE errors when
    # running `alembic upgrade head` from an empty DB. The proper fix is to
    # rewrite migration 001 with explicit DDL — deferred as out of scope.
    inspector = inspect(op.get_bind())
    existing_tables = inspector.get_table_names()

    if "contracts" not in existing_tables:
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

    if "weather_forecasts" not in existing_tables:
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

    # Backfill crypto contract rows. ON CONFLICT DO NOTHING makes this
    # idempotent even if one of the two rows already exists.
    op.execute(sa.text(
        "INSERT INTO contracts (symbol, venue, contract_metadata) VALUES "
        "('BTCUSDT', 'binance_us', '{}'), "
        "('ETHUSDT', 'binance_us', '{}') "
        "ON CONFLICT (symbol) DO NOTHING"
    ))


def downgrade():
    op.drop_table("weather_forecasts")
    op.drop_table("contracts")
