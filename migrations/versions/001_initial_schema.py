"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-06-29
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None


def upgrade():
    from loophedge.models import Base
    Base.metadata.create_all(op.get_bind())


def downgrade():
    from loophedge.models import Base
    Base.metadata.drop_all(op.get_bind())
