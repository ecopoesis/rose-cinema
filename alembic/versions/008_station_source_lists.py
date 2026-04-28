"""add source_artists, source_albums, source_tracks to stations

Revision ID: 008
Revises: 007
Create Date: 2026-04-28 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stations", sa.Column("source_artists", JSONB, nullable=True))
    op.add_column("stations", sa.Column("source_albums", JSONB, nullable=True))
    op.add_column("stations", sa.Column("source_tracks", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("stations", "source_tracks")
    op.drop_column("stations", "source_albums")
    op.drop_column("stations", "source_artists")
