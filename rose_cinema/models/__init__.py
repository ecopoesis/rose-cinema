from __future__ import annotations

import re
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date, Float, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


def slugify(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'station'


class DJ(Base):
    __tablename__ = "djs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tts_provider: Mapped[str] = mapped_column(String(50), nullable=False, default="piper")
    tts_voice_id: Mapped[str] = mapped_column(String(200), nullable=False, default="en_US-lessac-medium")
    tts_voice_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    stations: Mapped[list[Station]] = relationship(back_populates="dj")


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Station parameters
    length_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    dj_talk_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.3)
    dj_babble_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    dj_max_length_secs: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    # DJ assignment
    dj_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("djs.id"), nullable=True
    )

    max_playlists: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Music source — flexible: could be genre, playlist ID, artist list, etc.
    music_source: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Explicit source lists (names only — resolved at generation time)
    source_artists: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source_albums: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source_tracks: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    album_art: Mapped[str | None] = mapped_column(String(200), nullable=True)
    slug: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    cron_schedule: Mapped[str | None] = mapped_column(String(100), nullable=True)
    discovery_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    dj: Mapped[DJ | None] = relationship(back_populates="stations")
    playlist_runs: Mapped[list[PlaylistRun]] = relationship(
        back_populates="station", cascade="all, delete-orphan"
    )


class PlaylistRun(Base):
    """A generated playlist instance — one run of a station."""

    __tablename__ = "playlist_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    station_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stations.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending, generating, ready, playing, failed
    playlist_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ma_playlist_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generation_secs: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    station: Mapped[Station] = relationship(back_populates="playlist_runs")
    events: Mapped[list[GenerationEvent]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class GenerationEvent(Base):
    __tablename__ = "generation_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("playlist_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    step_type: Mapped[str] = mapped_column(String(50), nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    run: Mapped[PlaylistRun] = relationship(back_populates="events")


class ListenPosition(Base):
    __tablename__ = "listen_positions"
    __table_args__ = (
        UniqueConstraint("station_id", "uid", "listened_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    station_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("stations.id", ondelete="CASCADE"), nullable=False,
    )
    uid: Mapped[str] = mapped_column(String(200), nullable=False)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("playlist_runs.id", ondelete="CASCADE"), nullable=False,
    )
    entry_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    listened_date: Mapped[date] = mapped_column(Date, nullable=False, server_default=func.current_date())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
