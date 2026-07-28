"""widen symbol columns from 20 to 96 chars

Event-contract venues use tickers longer than 20 characters (a Kalshi ticker
such as KXHIGHNY-26JUL28-B82.5 is 22), and Polymarket CLOB token IDs are
uint256 values rendered in decimal. The old width truncates or errors on both.

Revision ID: 003
Revises: 002
Create Date: 2026-07-28
"""
import sqlalchemy as sa
from alembic import op

revision = "003"
down_revision = "002"

_TABLES = ("bars", "signals", "fills", "positions")


def upgrade():
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                "symbol",
                existing_type=sa.String(20),
                type_=sa.String(96),
                existing_nullable=False,
            )


def downgrade():
    for table in _TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(
                "symbol",
                existing_type=sa.String(96),
                type_=sa.String(20),
                existing_nullable=False,
            )
