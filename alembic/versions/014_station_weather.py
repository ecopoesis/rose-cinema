"""Add weather_postal_code and weather_rate to stations.

Revision ID: 014
Revises: 013
"""

from alembic import op
import sqlalchemy as sa

revision = "014"
down_revision = "013"


def upgrade() -> None:
    op.add_column("stations", sa.Column("weather_postal_code", sa.String(10), nullable=True))
    op.add_column("stations", sa.Column("weather_rate", sa.Float(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("stations", "weather_rate")
    op.drop_column("stations", "weather_postal_code")
