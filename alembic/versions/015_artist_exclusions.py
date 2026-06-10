"""Add stations.excluded_artists and the app_settings table.

Revision ID: 015
Revises: 014
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "015"
down_revision = "014"


def upgrade() -> None:
    op.add_column("stations", sa.Column("excluded_artists", JSONB, nullable=True))
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", JSONB, nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_column("stations", "excluded_artists")
