from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rose_cinema.models import GenerationEvent, PlaylistRun, Station, DJ

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
POLL_TIMEOUT = 5.0


class EventQueue:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def enqueue(
        self,
        session: AsyncSession,
        run_id: str,
        step_type: str,
        step_index: int = 0,
        payload: dict | None = None,
    ) -> str:
        event = GenerationEvent(
            run_id=run_id,
            step_type=step_type,
            step_index=step_index,
            payload=payload or {},
        )
        session.add(event)
        await session.flush()
        await session.execute(
            text("SELECT pg_notify('generation', :run_id)"),
            {"run_id": run_id},
        )
        logger.info("[%s] enqueued %s[%d]", run_id[:8], step_type, step_index)
        return event.id

    async def claim_next(self, session: AsyncSession) -> GenerationEvent | None:
        result = await session.execute(
            select(GenerationEvent)
            .where(GenerationEvent.status == "pending")
            .order_by(GenerationEvent.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        event = result.scalar_one_or_none()
        if event:
            event.status = "processing"
            event.started_at = _utcnow()
            await session.flush()
        return event

    async def complete(
        self, session: AsyncSession, event: GenerationEvent, result: dict,
    ) -> None:
        event.status = "completed"
        event.result = result
        event.completed_at = _utcnow()
        await session.flush()
        logger.info(
            "[%s] completed %s[%d]",
            event.run_id[:8], event.step_type, event.step_index,
        )

    async def fail(
        self, session: AsyncSession, event: GenerationEvent, error: str,
    ) -> None:
        event.retry_count += 1
        event.error_message = error
        if event.retry_count < MAX_RETRIES:
            event.status = "pending"
            event.started_at = None
            logger.warning(
                "[%s] %s[%d] failed (attempt %d/%d): %s — retrying",
                event.run_id[:8], event.step_type, event.step_index,
                event.retry_count, MAX_RETRIES, error,
            )
        else:
            event.status = "failed"
            event.completed_at = _utcnow()
            logger.error(
                "[%s] %s[%d] permanently failed: %s",
                event.run_id[:8], event.step_type, event.step_index, error,
            )
            run = await session.get(PlaylistRun, event.run_id)
            if run:
                run.status = "failed"
                run.error_message = f"{event.step_type}: {error}"
        await session.flush()

    async def cancel_run(self, session: AsyncSession, run_id: str) -> int:
        result = await session.execute(
            update(GenerationEvent)
            .where(
                GenerationEvent.run_id == run_id,
                GenerationEvent.status.in_(["pending", "processing"]),
            )
            .values(status="cancelled")
        )
        await session.flush()
        return result.rowcount

    async def get_progress(self, session: AsyncSession, run_id: str) -> dict:
        result = await session.execute(
            select(
                GenerationEvent.status,
                GenerationEvent.step_type,
            )
            .where(GenerationEvent.run_id == run_id)
        )
        rows = result.all()
        if not rows:
            return {}
        total = len(rows)
        completed = sum(1 for s, _ in rows if s == "completed")
        failed = sum(1 for s, _ in rows if s == "failed")
        processing = [t for s, t in rows if s == "processing"]
        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "current_step": processing[0] if processing else None,
        }


async def enqueue_ma_chain(
    session: AsyncSession, queue: EventQueue, run: PlaylistRun,
) -> None:
    import json
    from pathlib import Path
    from rose_cinema.config import settings

    station = await session.get(Station, run.station_id)
    station_name = station.name if station else "Unknown"
    dj_name = "?"
    art_url = None
    if station:
        if station.dj_id:
            dj = await session.get(DJ, station.dj_id)
            if dj:
                dj_name = dj.name
        if station.album_art and (Path(settings.album_art_dir) / station.album_art).exists():
            art_url = f"{settings.public_base_url.rstrip('/')}/api/stations/{station.id}/album-art"

    base = settings.public_base_url.rstrip("/") if settings.public_base_url else ""
    entries = json.loads(run.playlist_json) if run.playlist_json else []
    dj_part = 0

    for entry in entries:
        if entry.get("type") == "dj" and entry.get("audio_file"):
            fname = Path(entry["audio_file"]).name
            url = f"{base}/audio/{fname}"
            ep = run.episode
            if ep and station:
                seg_name = f"{station_name} - Episode {ep} - Intro" if dj_part == 0 else f"{station_name} - Episode {ep} - Part {dj_part}"
            else:
                seg_name = f"{station_name} - DJ {dj_part}"
            await queue.enqueue(
                session, run.id, "ma_ingest_dj", dj_part,
                {
                    "run_id": run.id,
                    "audio_url": url,
                    "segment_name": seg_name,
                    "dj_name": dj_name,
                    "art_url": art_url,
                },
            )
            dj_part += 1

    if dj_part == 0:
        await queue.enqueue(
            session, run.id, "ma_create_playlist", 0,
            {"run_id": run.id},
        )


class QueueWorker:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory
        self._queue = EventQueue(session_factory)
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run(), name="queue-worker")
        logger.info("Queue worker started")

    async def stop(self) -> None:
        self._running = False
        self._wake.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Queue worker stopped")

    async def _run(self) -> None:
        await self._recover_stale()
        await self._setup_listener()

        while self._running:
            try:
                async with self._sf() as session:
                    event = await self._queue.claim_next(session)
                    if event:
                        await session.commit()
                        await self._process(event)
                        continue
                    await session.rollback()
            except Exception:
                logger.exception("Queue worker error during claim")

            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=POLL_TIMEOUT)
            except asyncio.TimeoutError:
                pass

    async def _setup_listener(self) -> None:
        try:
            import asyncpg
            from rose_cinema.config import settings
            dsn = settings.database_url.replace("+asyncpg", "")
            self._listen_conn = await asyncpg.connect(dsn=dsn)
            await self._listen_conn.add_listener("generation", self._on_notify)
            logger.info("LISTEN generation channel active")
        except Exception:
            logger.warning("LISTEN setup failed, falling back to polling", exc_info=True)

    def _on_notify(self, conn, pid, channel, payload) -> None:
        self._wake.set()

    async def _recover_stale(self) -> None:
        async with self._sf() as session:
            result = await session.execute(
                update(GenerationEvent)
                .where(GenerationEvent.status == "processing")
                .values(status="pending", started_at=None)
                .returning(GenerationEvent.id)
            )
            stale = result.scalars().all()
            if stale:
                logger.warning("Recovered %d stale events", len(stale))
            await session.commit()

        await self._recover_broken_chains()

    async def _recover_broken_chains(self) -> None:
        async with self._sf() as session:
            runs = await session.execute(
                select(PlaylistRun)
                .where(PlaylistRun.status == "generating")
            )
            for run in runs.scalars().all():
                events = await session.execute(
                    select(GenerationEvent)
                    .where(GenerationEvent.run_id == run.id)
                )
                all_events = events.scalars().all()
                has_pending = any(e.status in ("pending", "processing") for e in all_events)
                if not has_pending and all_events:
                    completed = [e for e in all_events if e.status == "completed"]
                    if completed:
                        last = max(completed, key=lambda e: e.completed_at or e.created_at)
                        logger.warning(
                            "[%s] recovering broken chain from %s",
                            run.id[:8], last.step_type,
                        )
                        await self._dispatch_next(session, last)
                        await session.commit()

    async def _process(self, event: GenerationEvent) -> None:
        from rose_cinema.services.step_handlers import HANDLERS

        handler = HANDLERS.get(event.step_type)
        if not handler:
            async with self._sf() as session:
                ev = await session.get(GenerationEvent, event.id)
                await self._queue.fail(session, ev, f"unknown step_type: {event.step_type}")
                await session.commit()
            return

        try:
            result = await handler(event.payload or {})
        except Exception as exc:
            async with self._sf() as session:
                ev = await session.get(GenerationEvent, event.id)
                await self._queue.fail(session, ev, str(exc)[:500])
                await session.commit()
            return

        async with self._sf() as session:
            ev = await session.get(GenerationEvent, event.id)
            await self._queue.complete(session, ev, result)
            await self._dispatch_next(session, ev)
            await session.commit()

    async def _dispatch_next(
        self, session: AsyncSession, completed: GenerationEvent,
    ) -> None:
        step = completed.step_type
        result = completed.result or {}
        run_id = completed.run_id

        if step == "pick_tracks":
            await self._after_pick_tracks(session, run_id, result)
        elif step == "generate_intro_script":
            await self._after_generate_script(session, completed, "synthesize_intro")
        elif step == "generate_transition":
            await self._after_generate_script(session, completed, "synthesize_transition")
        elif step in ("synthesize_intro", "synthesize_transition"):
            await self._maybe_finalize(session, run_id)
        elif step == "finalize_playlist":
            await self._after_finalize(session, run_id)
        elif step == "download_tracks":
            await self._after_download_tracks(session, run_id)
        elif step == "ma_ingest_dj":
            await self._maybe_ma_create_playlist(session, run_id)
        elif step == "ma_create_playlist":
            await self._after_ma_create_playlist(session, completed)
        elif step == "ma_add_tracks":
            await self._after_ma_add_tracks(session, completed)
        elif step == "ma_cleanup":
            run = await session.get(PlaylistRun, run_id)
            if run:
                run.status = "saved"

    async def _after_pick_tracks(
        self, session: AsyncSession, run_id: str, result: dict,
    ) -> None:
        songs = result.get("songs", [])
        run = await session.get(PlaylistRun, run_id)
        if not run:
            return
        station = await session.get(Station, run.station_id)
        if not station:
            return
        dj = await session.get(DJ, station.dj_id) if station.dj_id else None

        if station.dj_talk_rate > 0 and dj:
            await self._queue.enqueue(
                session, run_id, "generate_intro_script", 0,
                {
                    "dj_id": dj.id,
                    "station_name": station.name,
                    "babble_rate": station.dj_babble_rate,
                    "max_secs": station.dj_max_length_secs,
                },
            )

        talk_decisions: list[bool] = []
        for i, song in enumerate(songs):
            should_talk = (
                i > 0
                and dj is not None
                and random.random() < station.dj_talk_rate
            )
            talk_decisions.append(should_talk)
            if should_talk:
                prev_song = songs[i - 1]
                await self._queue.enqueue(
                    session, run_id, "generate_transition", i,
                    {
                        "dj_id": dj.id,
                        "previous_song": prev_song,
                        "next_song": song,
                        "babble_rate": station.dj_babble_rate,
                        "max_secs": station.dj_max_length_secs,
                        "song_index": i,
                    },
                )

        has_dj_work = station.dj_talk_rate > 0 and dj and any(talk_decisions)
        has_intro = station.dj_talk_rate > 0 and dj

        if not has_dj_work and not has_intro:
            await self._queue.enqueue(
                session, run_id, "finalize_playlist", 0,
                {"run_id": run_id},
            )

    async def _after_generate_script(
        self,
        session: AsyncSession,
        completed: GenerationEvent,
        synth_type: str,
    ) -> None:
        run_id = completed.run_id
        result = completed.result or {}
        payload = completed.payload or {}

        script = (result.get("script") or "").strip()
        if not script:
            logger.warning("Empty script from %s — skipping %s", completed.step_type, synth_type)
            await self._maybe_finalize(session, run_id)
            return

        run = await session.get(PlaylistRun, run_id)
        if not run:
            return
        station = await session.get(Station, run.station_id)
        dj = await session.get(DJ, payload["dj_id"]) if payload.get("dj_id") else None
        if not dj:
            return

        synth_payload = {
            "script": script,
            "dj_id": dj.id,
            "tts_provider": dj.tts_provider,
            "tts_voice_id": dj.tts_voice_id,
            "tts_voice_ref": dj.tts_voice_ref or "",
            "run_prefix": run_id[:8],
            "station_name": station.name if station else "",
            "episode": run.episode,
        }
        if synth_type == "synthesize_transition":
            synth_payload["song_index"] = payload.get("song_index", 0)
            synth_payload["segment_number"] = completed.step_index

        await self._queue.enqueue(
            session, run_id, synth_type, completed.step_index, synth_payload,
        )

    async def _maybe_finalize(self, session: AsyncSession, run_id: str) -> None:
        result = await session.execute(
            select(GenerationEvent)
            .where(
                GenerationEvent.run_id == run_id,
                GenerationEvent.step_type.in_([
                    "generate_intro_script", "synthesize_intro",
                    "generate_transition", "synthesize_transition",
                ]),
            )
        )
        events = result.scalars().all()
        if not events:
            return
        all_done = all(e.status == "completed" for e in events)
        if all_done:
            existing = await session.execute(
                select(GenerationEvent)
                .where(
                    GenerationEvent.run_id == run_id,
                    GenerationEvent.step_type == "finalize_playlist",
                )
            )
            if not existing.scalar_one_or_none():
                await self._queue.enqueue(
                    session, run_id, "finalize_playlist", 0,
                    {"run_id": run_id},
                )

    async def _after_finalize(self, session: AsyncSession, run_id: str) -> None:
        from rose_cinema.config import settings as cfg

        run = await session.get(PlaylistRun, run_id)
        if not run:
            return

        if cfg.apple_music_user_token:
            await self._queue.enqueue(
                session, run_id, "download_tracks", 0,
                {"run_id": run_id},
            )
        else:
            await self._after_download_tracks(session, run_id)

    async def _after_download_tracks(self, session: AsyncSession, run_id: str) -> None:
        from rose_cinema.services.music_assistant import get_music_assistant_client

        run = await session.get(PlaylistRun, run_id)
        if not run:
            return

        client = get_music_assistant_client()
        if not client:
            run.status = "ready"
            return

        await self._enqueue_ma_chain(session, run)

    async def _enqueue_ma_chain(
        self, session: AsyncSession, run: PlaylistRun,
    ) -> None:
        await enqueue_ma_chain(session, self._queue, run)

    async def _maybe_ma_create_playlist(
        self, session: AsyncSession, run_id: str,
    ) -> None:
        result = await session.execute(
            select(GenerationEvent)
            .where(
                GenerationEvent.run_id == run_id,
                GenerationEvent.step_type == "ma_ingest_dj",
            )
        )
        events = result.scalars().all()
        if not events:
            return
        all_done = all(e.status == "completed" for e in events)
        if all_done:
            existing = await session.execute(
                select(GenerationEvent)
                .where(
                    GenerationEvent.run_id == run_id,
                    GenerationEvent.step_type == "ma_create_playlist",
                )
            )
            if not existing.scalar_one_or_none():
                await self._queue.enqueue(
                    session, run_id, "ma_create_playlist", 0,
                    {"run_id": run_id},
                )

    async def _after_ma_create_playlist(
        self, session: AsyncSession, completed: GenerationEvent,
    ) -> None:
        result = completed.result or {}
        run_id = completed.run_id
        playlist_id = result.get("playlist_id")
        if not playlist_id:
            return

        import json
        from pathlib import Path
        from rose_cinema.config import settings

        run = await session.get(PlaylistRun, run_id)
        if not run:
            return

        ingest_events = await session.execute(
            select(GenerationEvent)
            .where(
                GenerationEvent.run_id == run_id,
                GenerationEvent.step_type == "ma_ingest_dj",
                GenerationEvent.status == "completed",
            )
            .order_by(GenerationEvent.step_index)
        )
        url_to_uri: dict[str, str] = {}
        for ev in ingest_events.scalars().all():
            ev_result = ev.result or {}
            if ev_result.get("url") and ev_result.get("library_uri"):
                url_to_uri[ev_result["url"]] = ev_result["library_uri"]

        base = settings.public_base_url.rstrip("/") if settings.public_base_url else ""
        entries = json.loads(run.playlist_json) if run.playlist_json else []
        ordered_uris: list[str] = []
        for entry in entries:
            if entry.get("type") == "song" and entry.get("apple_music_id"):
                ordered_uris.append(f"apple_music://track/{entry['apple_music_id']}")
            elif entry.get("type") == "dj" and entry.get("audio_file"):
                fname = Path(entry["audio_file"]).name
                url = f"{base}/audio/{fname}"
                ordered_uris.append(url_to_uri.get(url, url))

        await self._queue.enqueue(
            session, run_id, "ma_add_tracks", 0,
            {
                "run_id": run_id,
                "playlist_id": playlist_id,
                "ordered_uris": ordered_uris,
            },
        )

    async def _after_ma_add_tracks(
        self, session: AsyncSession, completed: GenerationEvent,
    ) -> None:
        run = await session.get(PlaylistRun, completed.run_id)
        if not run:
            return
        station = await session.get(Station, run.station_id)
        max_playlists = station.max_playlists if station else 0
        if max_playlists > 0:
            await self._queue.enqueue(
                session, completed.run_id, "ma_cleanup", 0,
                {
                    "run_id": completed.run_id,
                    "station_id": run.station_id,
                    "max_playlists": max_playlists,
                },
            )
        else:
            run.status = "saved"
