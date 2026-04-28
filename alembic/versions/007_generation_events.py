"""add generation_events queue table

Revision ID: 007
Revises: 006
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "007"
down_revision = "006"


def upgrade() -> None:
    op.create_table(
        "generation_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("playlist_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_type", sa.String(50), nullable=False),
        sa.Column("step_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column("result", postgresql.JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_gen_events_run_id", "generation_events", ["run_id"])
    op.create_index("ix_gen_events_status", "generation_events", ["status"])


def downgrade() -> None:
    op.drop_index("ix_gen_events_status")
    op.drop_index("ix_gen_events_run_id")
    op.drop_table("generation_events")
