"""Add playlist_runs.llm_input_tokens / llm_output_tokens — per-run LLM token usage.

Revision ID: 019
Revises: 018
"""

from alembic import op
import sqlalchemy as sa

revision = "019"
down_revision = "018"


def upgrade() -> None:
    op.add_column(
        "playlist_runs",
        sa.Column("llm_input_tokens", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "playlist_runs",
        sa.Column("llm_output_tokens", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("playlist_runs", "llm_output_tokens")
    op.drop_column("playlist_runs", "llm_input_tokens")
