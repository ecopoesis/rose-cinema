from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rose_cinema.models import DJ, Station, PlaylistRun
from rose_cinema.repositories import (
    DJRecord,
    DJRepository,
    StationRecord,
    StationRepository,
    PlaylistRunRecord,
    PlaylistRunRepository,
)


# ── Helpers ────────────────────────────────────────────────────────────


def _dj_to_record(dj: DJ) -> DJRecord:
    return DJRecord(
        id=dj.id,
        name=dj.name,
        agent_md=dj.agent_md,
        tts_provider=dj.tts_provider,
        tts_voice_id=dj.tts_voice_id,
    )


def _station_to_record(s: Station) -> StationRecord:
    return StationRecord(
        id=s.id,
        name=s.name,
        description=s.description,
        length_minutes=s.length_minutes,
        dj_talk_rate=s.dj_talk_rate,
        dj_babble_rate=s.dj_babble_rate,
        dj_max_length_secs=s.dj_max_length_secs,
        max_playlists=s.max_playlists,
        dj_id=s.dj_id,
        music_source=s.music_source,
    )


def _run_to_record(r: PlaylistRun) -> PlaylistRunRecord:
    return PlaylistRunRecord(
        id=r.id,
        station_id=r.station_id,
        status=r.status,
        playlist_json=r.playlist_json,
        error_message=r.error_message,
        ma_playlist_id=r.ma_playlist_id,
    )


# ── DJ ─────────────────────────────────────────────────────────────────


class SqliteDJRepository(DJRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, dj_id: str) -> DJRecord | None:
        obj = await self._session.get(DJ, dj_id)
        return _dj_to_record(obj) if obj else None

    async def list_all(self) -> list[DJRecord]:
        result = await self._session.execute(select(DJ).order_by(DJ.name))
        return [_dj_to_record(r) for r in result.scalars().all()]

    async def create(self, record: DJRecord) -> DJRecord:
        obj = DJ(
            name=record.name,
            agent_md=record.agent_md,
            tts_provider=record.tts_provider,
            tts_voice_id=record.tts_voice_id,
        )
        self._session.add(obj)
        await self._session.commit()
        await self._session.refresh(obj)
        return _dj_to_record(obj)

    async def update(self, record: DJRecord) -> DJRecord:
        obj = await self._session.get(DJ, record.id)
        if not obj:
            raise ValueError(f"DJ {record.id} not found")
        obj.name = record.name
        obj.agent_md = record.agent_md
        obj.tts_provider = record.tts_provider
        obj.tts_voice_id = record.tts_voice_id
        await self._session.commit()
        await self._session.refresh(obj)
        return _dj_to_record(obj)

    async def delete(self, dj_id: str) -> bool:
        obj = await self._session.get(DJ, dj_id)
        if not obj:
            return False
        await self._session.delete(obj)
        await self._session.commit()
        return True


# ── Station ────────────────────────────────────────────────────────────


class SqliteStationRepository(StationRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, station_id: str) -> StationRecord | None:
        obj = await self._session.get(Station, station_id)
        return _station_to_record(obj) if obj else None

    async def list_all(self) -> list[StationRecord]:
        result = await self._session.execute(select(Station).order_by(Station.name))
        return [_station_to_record(r) for r in result.scalars().all()]

    async def create(self, record: StationRecord) -> StationRecord:
        obj = Station(
            name=record.name,
            description=record.description,
            length_minutes=record.length_minutes,
            dj_talk_rate=record.dj_talk_rate,
            dj_babble_rate=record.dj_babble_rate,
            dj_max_length_secs=record.dj_max_length_secs,
            max_playlists=record.max_playlists,
            dj_id=record.dj_id,
            music_source=record.music_source,
        )
        self._session.add(obj)
        await self._session.commit()
        await self._session.refresh(obj)
        return _station_to_record(obj)

    async def update(self, record: StationRecord) -> StationRecord:
        obj = await self._session.get(Station, record.id)
        if not obj:
            raise ValueError(f"Station {record.id} not found")
        obj.name = record.name
        obj.description = record.description
        obj.length_minutes = record.length_minutes
        obj.dj_talk_rate = record.dj_talk_rate
        obj.dj_babble_rate = record.dj_babble_rate
        obj.dj_max_length_secs = record.dj_max_length_secs
        obj.max_playlists = record.max_playlists
        obj.dj_id = record.dj_id
        obj.music_source = record.music_source
        await self._session.commit()
        await self._session.refresh(obj)
        return _station_to_record(obj)

    async def delete(self, station_id: str) -> bool:
        obj = await self._session.get(Station, station_id)
        if not obj:
            return False
        await self._session.delete(obj)
        await self._session.commit()
        return True


# ── PlaylistRun ────────────────────────────────────────────────────────


class SqlitePlaylistRunRepository(PlaylistRunRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, run_id: str) -> PlaylistRunRecord | None:
        obj = await self._session.get(PlaylistRun, run_id)
        return _run_to_record(obj) if obj else None

    async def list_by_station(self, station_id: str) -> list[PlaylistRunRecord]:
        result = await self._session.execute(
            select(PlaylistRun)
            .where(PlaylistRun.station_id == station_id)
            .order_by(PlaylistRun.created_at.desc())
        )
        return [_run_to_record(r) for r in result.scalars().all()]

    async def create(self, record: PlaylistRunRecord) -> PlaylistRunRecord:
        obj = PlaylistRun(
            station_id=record.station_id,
            status=record.status,
            playlist_json=record.playlist_json,
        )
        self._session.add(obj)
        await self._session.commit()
        await self._session.refresh(obj)
        return _run_to_record(obj)

    async def update(self, record: PlaylistRunRecord) -> PlaylistRunRecord:
        obj = await self._session.get(PlaylistRun, record.id)
        if not obj:
            raise ValueError(f"PlaylistRun {record.id} not found")
        obj.status = record.status
        obj.playlist_json = record.playlist_json
        obj.error_message = record.error_message
        obj.ma_playlist_id = record.ma_playlist_id
        await self._session.commit()
        await self._session.refresh(obj)
        return _run_to_record(obj)

    async def delete(self, run_id: str) -> bool:
        obj = await self._session.get(PlaylistRun, run_id)
        if not obj:
            return False
        await self._session.delete(obj)
        await self._session.commit()
        return True
