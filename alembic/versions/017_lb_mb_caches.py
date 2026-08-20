"""Artist link, ListenBrainz similar-artists, and recording-resolution caches.

Revision ID: 017
Revises: 016
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "017"
down_revision = "016"


def upgrade() -> None:
    op.create_table(
        "artist_links",
        sa.Column("name_key", sa.String(300), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("mbid", sa.String(36), nullable=True),
        sa.Column("apple_music_id", sa.String(36), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_artist_links_mbid", "artist_links", ["mbid"])
    op.create_table(
        "lb_similar_cache",
        sa.Column("artist_mbid", sa.String(36), primary_key=True),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "recording_resolutions",
        sa.Column("recording_mbid", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False, server_default=""),
        sa.Column("artist", sa.String(500), nullable=False, server_default=""),
        sa.Column("apple_music_id", sa.String(36), nullable=True),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("resolved_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("recording_resolutions")
    op.drop_table("lb_similar_cache")
    op.drop_index("ix_artist_links_mbid", table_name="artist_links")
    op.drop_table("artist_links")
