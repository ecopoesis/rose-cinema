"""add cron_schedule to stations

Revision ID: 009
Revises: 008
Create Date: 2026-04-28 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stations", sa.Column("cron_schedule", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("stations", "cron_schedule")
