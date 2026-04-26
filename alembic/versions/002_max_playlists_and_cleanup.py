"""max_playlists and MA cleanup tracking

Revision ID: 002
Revises: 001
Create Date: 2026-04-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stations",
        sa.Column("max_playlists", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "playlist_runs",
        sa.Column("ma_playlist_id", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("playlist_runs", "ma_playlist_id")
    op.drop_column("stations", "max_playlists")
