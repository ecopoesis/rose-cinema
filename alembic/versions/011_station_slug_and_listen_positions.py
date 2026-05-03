"""add station slug and listen_positions table

Revision ID: 011
Revises: 010
Create Date: 2026-05-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stations", sa.Column("slug", sa.String(200), nullable=True))
    op.execute(
        "UPDATE stations SET slug = lower(regexp_replace(name, '[^a-zA-Z0-9]+', '-', 'g'))"
    )
    op.execute("UPDATE stations SET slug = trim(both '-' from slug)")
    op.execute("UPDATE stations SET slug = 'station' WHERE slug = '' OR slug IS NULL")
    # Deduplicate slugs by appending row_number for collisions
    op.execute("""
        UPDATE stations SET slug = slug || '-' || sub.rn
        FROM (
            SELECT id, row_number() OVER (PARTITION BY slug ORDER BY created_at) AS rn, slug AS s
            FROM stations
        ) sub
        WHERE stations.id = sub.id AND sub.rn > 1
    """)
    op.alter_column("stations", "slug", nullable=False)
    op.create_unique_constraint("uq_stations_slug", "stations", ["slug"])

    op.create_table(
        "listen_positions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("station_id", sa.String(36), sa.ForeignKey("stations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uid", sa.String(200), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("playlist_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entry_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("listened_date", sa.Date, nullable=False, server_default=sa.text("CURRENT_DATE")),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("station_id", "uid", "listened_date"),
    )
    op.create_index("ix_listen_positions_station_uid", "listen_positions", ["station_id", "uid"])


def downgrade() -> None:
    op.drop_index("ix_listen_positions_station_uid", table_name="listen_positions")
    op.drop_table("listen_positions")
    op.drop_constraint("uq_stations_slug", "stations", type_="unique")
    op.drop_column("stations", "slug")
