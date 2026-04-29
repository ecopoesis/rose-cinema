from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rose_cinema.models import PlaylistRun, Station
from rose_cinema.repositories import PlaylistRunRecord
from rose_cinema.repositories.sql import SqlPlaylistRunRepository
from rose_cinema.services.queue import EventQueue

logger = logging.getLogger(__name__)


class CronScheduler:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run(), name="cron-scheduler")
        logger.info("Cron scheduler started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Cron scheduler stopped")

    async def _run(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("Cron scheduler tick failed")
            await asyncio.sleep(60)

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with self._sf() as session:
            result = await session.execute(
                select(Station).where(Station.cron_schedule.isnot(None))
            )
            stations = result.scalars().all()

        for station in stations:
            try:
                await self._check_station(station, now)
            except Exception:
                logger.exception("Cron check failed for station %s", station.id[:8])

    async def _check_station(self, station: Station, now: datetime) -> None:
        schedule = (station.cron_schedule or "").strip()
        if not schedule:
            return

        if not croniter.is_valid(schedule):
            logger.warning("Invalid cron for station %s: %s", station.id[:8], schedule)
            return

        if not station.dj_id or not station.music_source:
            return

        cron = croniter(schedule, now)
        last_due = cron.get_prev(datetime)

        async with self._sf() as session:
            exists = await session.execute(
                select(PlaylistRun.id)
                .where(
                    PlaylistRun.station_id == station.id,
                    PlaylistRun.created_at >= last_due,
                )
                .limit(1)
            )
            if exists.scalar_one_or_none():
                return

            generating = await session.execute(
                select(PlaylistRun.id)
                .where(
                    PlaylistRun.station_id == station.id,
                    PlaylistRun.status == "generating",
                )
                .limit(1)
            )
            if generating.scalar_one_or_none():
                return

            run_repo = SqlPlaylistRunRepository(session)
            run = await run_repo.create(
                PlaylistRunRecord(station_id=station.id, status="generating")
            )
            logger.info(
                "[%s] Cron triggered for station '%s' (schedule: %s)",
                station.id[:8], station.name, schedule,
            )

            exclude_ids = list(await run_repo.list_recent_track_ids(station.id, max_runs=3))
            queue = EventQueue(self._sf)
            await queue.enqueue(
                session, run.id, "pick_tracks", 0,
                {
                    "station_id": station.id,
                    "music_source": station.music_source,
                    "length_minutes": station.length_minutes,
                    "exclude_ids": exclude_ids,
                    "songs_override": None,
                    "source_artists": station.source_artists,
                    "source_albums": station.source_albums,
                    "source_tracks": station.source_tracks,
                },
            )
            await session.commit()
