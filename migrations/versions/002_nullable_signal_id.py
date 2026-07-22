"""make Fill.signal_id nullable

Revision ID: 002
Revises: 001
Create Date: 2026-06-29
"""
from alembic import op

revision = "002"
down_revision = "001"


def upgrade():
    with op.batch_alter_table("fills") as batch_op:
        batch_op.alter_column("signal_id", nullable=True)


def downgrade():
    with op.batch_alter_table("fills") as batch_op:
        batch_op.alter_column("signal_id", nullable=False)
