"""Add djs.voice_ticks — opt-in non-verbal expression tags ([laugh], [sigh], …).

Revision ID: 018
Revises: 017
"""

from alembic import op
import sqlalchemy as sa

revision = "018"
down_revision = "017"


def upgrade() -> None:
    op.add_column("djs", sa.Column("voice_ticks", sa.Boolean, nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("djs", "voice_ticks")
