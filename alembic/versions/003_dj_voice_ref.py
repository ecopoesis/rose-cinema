"""add tts_voice_ref to djs

Revision ID: 003
Revises: 002
Create Date: 2026-04-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("djs", sa.Column("tts_voice_ref", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("djs", "tts_voice_ref")
