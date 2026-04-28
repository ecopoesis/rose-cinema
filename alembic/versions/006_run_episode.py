"""add episode to playlist_runs

Revision ID: 006
Revises: 005
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"


def upgrade() -> None:
    op.add_column("playlist_runs", sa.Column("episode", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("playlist_runs", "episode")
