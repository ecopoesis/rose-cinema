"""add discovery_rate to stations

Revision ID: 010
Revises: 009
Create Date: 2026-04-30 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stations", sa.Column("discovery_rate", sa.Float, nullable=False, server_default="0.5"))


def downgrade() -> None:
    op.drop_column("stations", "discovery_rate")
