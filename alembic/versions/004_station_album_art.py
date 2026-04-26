"""add album_art to stations

Revision ID: 004
Revises: 003
Create Date: 2026-04-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stations", sa.Column("album_art", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("stations", "album_art")
