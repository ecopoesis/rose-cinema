"""Add history_runs to stations for no-repeat memory window.

Revision ID: 012
Revises: 011
"""

from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"


def upgrade() -> None:
    op.add_column(
        "stations",
        sa.Column("history_runs", sa.Integer, nullable=False, server_default="3"),
    )


def downgrade() -> None:
    op.drop_column("stations", "history_runs")
