"""Track cache with Picard-style file naming.

Revision ID: 013
Revises: 012
"""

from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"


def upgrade() -> None:
    op.create_table(
        "cached_tracks",
        sa.Column("apple_music_id", sa.String(36), primary_key=True),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("artist", sa.String(500), nullable=False, server_default=""),
        sa.Column("album", sa.String(500), nullable=False, server_default=""),
        sa.Column("title", sa.String(500), nullable=False, server_default=""),
        sa.Column("year", sa.String(4), nullable=False, server_default=""),
        sa.Column("track_number", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("cached_tracks")
