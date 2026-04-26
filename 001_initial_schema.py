"""initial schema

Revision ID: 001
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "djs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("agent_md", sa.Text, nullable=False, server_default=""),
        sa.Column("tts_provider", sa.String(50), nullable=False, server_default="piper"),
        sa.Column("tts_voice_id", sa.String(200), nullable=False, server_default="en_US-lessac-medium"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "stations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("length_minutes", sa.Integer, nullable=False, server_default="60"),
        sa.Column("dj_talk_rate", sa.Float, nullable=False, server_default="0.3"),
        sa.Column("dj_babble_rate", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("dj_max_length_secs", sa.Integer, nullable=False, server_default="30"),
        sa.Column("dj_id", sa.String(36), sa.ForeignKey("djs.id"), nullable=True),
        sa.Column("music_source", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "playlist_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("station_id", sa.String(36), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("playlist_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("playlist_runs")
    op.drop_table("stations")
    op.drop_table("djs")
