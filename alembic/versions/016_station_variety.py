"""Add station variety sliders: genre_variety, year_variety, popularity_variety.

Revision ID: 016
Revises: 015
"""

from alembic import op
import sqlalchemy as sa

revision = "016"
down_revision = "015"


def upgrade() -> None:
    op.add_column("stations", sa.Column("genre_variety", sa.Float, nullable=False, server_default="0.5"))
    op.add_column("stations", sa.Column("year_variety", sa.Float, nullable=False, server_default="0.5"))
    op.add_column("stations", sa.Column("popularity_variety", sa.Float, nullable=False, server_default="0.0"))


def downgrade() -> None:
    op.drop_column("stations", "popularity_variety")
    op.drop_column("stations", "year_variety")
    op.drop_column("stations", "genre_variety")
