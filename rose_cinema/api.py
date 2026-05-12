from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from datetime import date

import httpx
from fastapi import FastAPI, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession

from rose_cinema.config import settings
from rose_cinema.database import async_session, get_session
from rose_cinema.repositories import DJRecord, StationRecord, PlaylistRunRecord
from rose_cinema.repositories.sql import (
    SqlDJRepository,
    SqlStationRepository,
    SqlPlaylistRunRepository,
    SqlListenPositionRepository,
)
from rose_cinema.repositories import ListenPositionRecord
from rose_cinema.services.music_assistant import get_music_assistant_client
from rose_cinema.services.queue import EventQueue, QueueWorker, enqueue_ma_chain
from rose_cinema.services.scheduler import CronScheduler
from rose_cinema.services.stream_manager import StreamManager
from rose_cinema.services.ezstream_manager import EzstreamManager
from rose_cinema.services.track_cache import TrackCache
from rose_cinema.services.apple_music_stream import AppleMusicStreamer
from rose_cinema.schemas import (
    DJCreate, DJUpdate, DJResponse,
    StationCreate, StationUpdate, StationResponse,
    GenerateRequest, PlaylistRunResponse, PlaylistEntryResponse,
    PlaylistRunSummary, ProgressResponse, EventResponse,
    MAPlayRequest, MAPlayResponse,
    DJExport, StationExport, ExportData, ImportResult,
    TestSongResponse, TestPlaylistResponse,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_worker: QueueWorker | None = None
_scheduler: CronScheduler | None = None
_stream_manager: StreamManager | None = None
_ezstream_mgr: EzstreamManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _worker, _scheduler, _stream_manager, _ezstream_mgr
    _worker = QueueWorker(async_session)
    await _worker.start()
    _scheduler = CronScheduler(async_session)
    await _scheduler.start()
    _stream_manager = StreamManager(async_session)
    await _stream_manager.start_background_tasks()
    _ezstream_mgr = EzstreamManager()
    yield
    await _ezstream_mgr.stop_all()
    _ezstream_mgr = None
    await _stream_manager.shutdown()
    _stream_manager = None
    await _scheduler.stop()
    _scheduler = None
    await _worker.stop()
    _worker = None


app = FastAPI(
    title="Rose Cinema",
    description="AI-powered radio station generator for Apple Music",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def fix_head_for_api(request, call_next):
    """StaticFiles(html=True) at / shadows HEAD for API routes."""
    if request.method == "HEAD" and request.url.path.startswith("/api/"):
        request.scope["method"] = "GET"
        response = await call_next(request)
        response.headers["content-length"] = str(response.headers.get("content-length", "0"))
        response.body = b""
        return response
    return await call_next(request)


# Serve DJ audio files
app.mount("/audio", StaticFiles(directory=settings.dj_audio_dir), name="audio")


# ── DJ endpoints ───────────────────────────────────────────────────────


@app.get("/api/djs", response_model=list[DJResponse])
async def list_djs(session: AsyncSession = Depends(get_session)):
    repo = SqlDJRepository(session)
    records = await repo.list_all()
    return [DJResponse(**r.__dict__) for r in records]


@app.get("/api/djs/{dj_id}", response_model=DJResponse)
async def get_dj(dj_id: str, session: AsyncSession = Depends(get_session)):
    repo = SqlDJRepository(session)
    record = await repo.get(dj_id)
    if not record:
        raise HTTPException(404, "DJ not found")
    return DJResponse(**record.__dict__)


@app.post("/api/djs", response_model=DJResponse, status_code=201)
async def create_dj(body: DJCreate, session: AsyncSession = Depends(get_session)):
    repo = SqlDJRepository(session)
    record = await repo.create(DJRecord(
        name=body.name,
        agent_md=body.agent_md,
        tts_provider=body.tts_provider,
        tts_voice_id=body.tts_voice_id,
        tts_voice_ref=body.tts_voice_ref,
    ))
    return DJResponse(**record.__dict__)


@app.put("/api/djs/{dj_id}", response_model=DJResponse)
async def update_dj(
    dj_id: str, body: DJUpdate, session: AsyncSession = Depends(get_session)
):
    repo = SqlDJRepository(session)
    existing = await repo.get(dj_id)
    if not existing:
        raise HTTPException(404, "DJ not found")

    if body.name is not None:
        existing.name = body.name
    if body.agent_md is not None:
        existing.agent_md = body.agent_md
    if body.tts_provider is not None:
        existing.tts_provider = body.tts_provider
    if body.tts_voice_id is not None:
        existing.tts_voice_id = body.tts_voice_id
    if body.tts_voice_ref is not None:
        existing.tts_voice_ref = body.tts_voice_ref

    updated = await repo.update(existing)
    return DJResponse(**updated.__dict__)


@app.delete("/api/djs/{dj_id}", status_code=204)
async def delete_dj(dj_id: str, session: AsyncSession = Depends(get_session)):
    repo = SqlDJRepository(session)
    if not await repo.get(dj_id):
        raise HTTPException(404, "DJ not found")
    station_repo = SqlStationRepository(session)
    for s in await station_repo.list_all():
        if s.dj_id == dj_id:
            s.dj_id = None
            await station_repo.update(s)
    await repo.delete(dj_id)


# ── Station endpoints ──────────────────────────────────────────────────


@app.get("/api/stations", response_model=list[StationResponse])
async def list_stations(session: AsyncSession = Depends(get_session)):
    repo = SqlStationRepository(session)
    records = await repo.list_all()
    return [StationResponse(**r.__dict__) for r in records]


@app.get("/api/stations/{station_id}", response_model=StationResponse)
async def get_station(station_id: str, session: AsyncSession = Depends(get_session)):
    repo = SqlStationRepository(session)
    record = await repo.get(station_id)
    if not record:
        raise HTTPException(404, "Station not found")
    return StationResponse(**record.__dict__)


@app.post("/api/stations", response_model=StationResponse, status_code=201)
async def create_station(
    body: StationCreate, session: AsyncSession = Depends(get_session)
):
    repo = SqlStationRepository(session)
    record = await repo.create(StationRecord(
        name=body.name,
        description=body.description,
        length_minutes=body.length_minutes,
        dj_talk_rate=body.dj_talk_rate,
        dj_babble_rate=body.dj_babble_rate,
        dj_max_length_secs=body.dj_max_length_secs,
        max_playlists=body.max_playlists,
        dj_id=body.dj_id,
        music_source=body.music_source,
        history_runs=body.history_runs,
    ))
    return StationResponse(**record.__dict__)


@app.put("/api/stations/{station_id}", response_model=StationResponse)
async def update_station(
    station_id: str, body: StationUpdate, session: AsyncSession = Depends(get_session)
):
    repo = SqlStationRepository(session)
    existing = await repo.get(station_id)
    if not existing:
        raise HTTPException(404, "Station not found")

    for field_name in (
        "name", "description", "length_minutes", "dj_talk_rate",
        "dj_babble_rate", "dj_max_length_secs", "max_playlists", "dj_id", "music_source",
        "source_artists", "source_albums", "source_tracks",
        "cron_schedule", "discovery_rate", "history_runs",
    ):
        val = getattr(body, field_name)
        if val is not None:
            setattr(existing, field_name, val)

    updated = await repo.update(existing)
    return StationResponse(**updated.__dict__)


@app.delete("/api/stations/{station_id}", status_code=204)
async def delete_station(
    station_id: str, session: AsyncSession = Depends(get_session)
):
    repo = SqlStationRepository(session)
    if not await repo.get(station_id):
        raise HTTPException(404, "Station not found")

    run_repo = SqlPlaylistRunRepository(session)
    runs = await run_repo.list_by_station(station_id)
    if runs:
        from rose_cinema.services.cleanup import cleanup_run
        ma_client = get_music_assistant_client()
        for run in runs:
            await cleanup_run(run, run_repo, settings.dj_audio_dir, ma_client)

    await repo.delete(station_id)


@app.post("/api/stations/{station_id}/album-art", response_model=StationResponse)
async def upload_album_art(
    station_id: str,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
):
    repo = SqlStationRepository(session)
    existing = await repo.get(station_id)
    if not existing:
        raise HTTPException(404, "Station not found")

    ext = Path(file.filename or "image.jpg").suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        raise HTTPException(400, "Only .jpg and .png files are supported")

    art_dir = Path(settings.album_art_dir)
    art_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{station_id}{ext}"
    dest = art_dir / filename
    dest.write_bytes(await file.read())

    existing.album_art = filename
    updated = await repo.update(existing)
    return StationResponse(**updated.__dict__)


@app.delete("/api/stations/{station_id}/album-art", response_model=StationResponse)
async def delete_album_art(
    station_id: str, session: AsyncSession = Depends(get_session),
):
    repo = SqlStationRepository(session)
    existing = await repo.get(station_id)
    if not existing:
        raise HTTPException(404, "Station not found")

    if existing.album_art:
        art_path = Path(settings.album_art_dir) / existing.album_art
        art_path.unlink(missing_ok=True)
    existing.album_art = ""
    updated = await repo.update(existing)
    return StationResponse(**updated.__dict__)


@app.get("/api/stations/{station_id}/album-art")
async def get_album_art(
    station_id: str, session: AsyncSession = Depends(get_session),
):
    repo = SqlStationRepository(session)
    existing = await repo.get(station_id)
    if not existing or not existing.album_art:
        raise HTTPException(404, "No album art")
    art_path = Path(settings.album_art_dir) / existing.album_art
    if not art_path.exists():
        raise HTTPException(404, "No album art")
    return FileResponse(art_path)


# ── Playlist generation ───────────────────────────────────────────────


@app.post("/api/generate", response_model=PlaylistRunResponse)
async def generate_playlist(
    body: GenerateRequest, session: AsyncSession = Depends(get_session)
):
    station_repo = SqlStationRepository(session)
    dj_repo = SqlDJRepository(session)
    run_repo = SqlPlaylistRunRepository(session)

    station = await station_repo.get(body.station_id)
    if not station:
        raise HTTPException(404, "Station not found")

    if not station.dj_id:
        raise HTTPException(400, "Station has no DJ assigned")

    dj = await dj_repo.get(station.dj_id)
    if not dj:
        raise HTTPException(404, "Assigned DJ not found")

    if not body.songs and not station.music_source:
        raise HTTPException(400, "Station has no music_source and no songs were provided")

    run = await run_repo.create(PlaylistRunRecord(station_id=station.id, status="generating"))
    logger.info("[%s] Starting generation for station '%s' (DJ: %s)", station.id[:8], station.name, dj.name)

    exclude_ids = (
        list(await run_repo.list_recent_track_ids(station.id, max_runs=station.history_runs))
        if station.history_runs > 0 else []
    )

    songs_override = None
    if body.songs:
        songs_override = [
            {
                "title": s.title, "artist": s.artist, "album": s.album,
                "year": s.year, "apple_music_id": s.apple_music_id,
                "duration_secs": s.duration_secs,
            }
            for s in body.songs
        ]

    queue = EventQueue(async_session)
    await queue.enqueue(
        session, run.id, "pick_tracks", 0,
        {
            "station_id": station.id,
            "music_source": station.music_source,
            "length_minutes": station.length_minutes,
            "exclude_ids": exclude_ids,
            "songs_override": songs_override,
            "source_artists": station.source_artists,
            "source_albums": station.source_albums,
            "source_tracks": station.source_tracks,
            "discovery_rate": station.discovery_rate,
        },
    )
    await session.commit()

    return PlaylistRunResponse(
        id=run.id,
        station_id=run.station_id,
        status=run.status,
        episode=run.episode,
    )


@app.get("/api/runs/{run_id}", response_model=PlaylistRunResponse)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)):
    repo = SqlPlaylistRunRepository(session)
    run = await repo.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    entries = []
    if run.playlist_json:
        entries = [PlaylistEntryResponse(**e) for e in json.loads(run.playlist_json)]

    progress = None
    if run.status == "generating":
        queue = EventQueue(async_session)
        prog = await queue.get_progress(session, run_id)
        if prog:
            progress = ProgressResponse(**prog)

    return PlaylistRunResponse(
        id=run.id,
        station_id=run.station_id,
        status=run.status,
        episode=run.episode,
        entries=entries,
        error_message=run.error_message,
        generation_secs=run.generation_secs,
        progress=progress,
    )


@app.get("/api/stations/{station_id}/runs", response_model=list[PlaylistRunSummary])
async def list_station_runs(
    station_id: str, session: AsyncSession = Depends(get_session)
):
    station_repo = SqlStationRepository(session)
    if not await station_repo.get(station_id):
        raise HTTPException(404, "Station not found")

    run_repo = SqlPlaylistRunRepository(session)
    runs = await run_repo.list_by_station(station_id)
    return [
        PlaylistRunSummary(
            id=r.id,
            station_id=r.station_id,
            status=r.status,
            episode=r.episode,
            created_at=r.created_at,
            track_count=len([
                e for e in json.loads(r.playlist_json)
                if e.get("type") == "song"
            ]) if r.playlist_json else 0,
            error_message=r.error_message,
            generation_secs=r.generation_secs,
        )
        for r in runs
    ]


@app.get("/api/runs/{run_id}/events", response_model=list[EventResponse])
async def get_run_events(run_id: str, session: AsyncSession = Depends(get_session)):
    from rose_cinema.models import GenerationEvent
    from sqlalchemy import select

    repo = SqlPlaylistRunRepository(session)
    run = await repo.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    result = await session.execute(
        select(GenerationEvent)
        .where(GenerationEvent.run_id == run_id)
        .order_by(GenerationEvent.created_at)
    )
    events = result.scalars().all()
    return [
        EventResponse(
            id=e.id,
            step_type=e.step_type,
            step_index=e.step_index,
            status=e.status,
            error_message=e.error_message,
            retry_count=e.retry_count,
            created_at=str(e.created_at) if e.created_at else None,
            started_at=str(e.started_at) if e.started_at else None,
            completed_at=str(e.completed_at) if e.completed_at else None,
        )
        for e in events
    ]


@app.delete("/api/runs/{run_id}", status_code=204)
async def delete_run(run_id: str, session: AsyncSession = Depends(get_session)):
    run_repo = SqlPlaylistRunRepository(session)
    run = await run_repo.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    from rose_cinema.services.cleanup import cleanup_run
    ma_client = get_music_assistant_client()
    await cleanup_run(run, run_repo, settings.dj_audio_dir, ma_client)


# ── Test playlist (track-only preview) ────────────────────────────────


@app.post(
    "/api/stations/{station_id}/test-playlist",
    response_model=TestPlaylistResponse,
)
async def test_playlist(
    station_id: str, session: AsyncSession = Depends(get_session),
):
    station_repo = SqlStationRepository(session)
    station = await station_repo.get(station_id)
    if not station:
        raise HTTPException(404, "Station not found")
    if not station.music_source:
        raise HTTPException(400, "Station has no music_source")

    import time as _time

    from rose_cinema.providers.factory import get_llm_provider
    from rose_cinema.services.musickit import get_music_catalog
    from rose_cinema.services.musicbrainz import get_musicbrainz_client
    from rose_cinema.services.seed_pool import SeedPoolBuilder
    from rose_cinema.services.track_picker import TrackPicker

    t0 = _time.monotonic()
    llm = get_llm_provider()
    catalog = get_music_catalog()
    mb_client = get_musicbrainz_client()
    seed_builder = SeedPoolBuilder(catalog, llm, mb_client) if catalog else None

    run_repo = SqlPlaylistRunRepository(session)
    exclude_ids = (
        set(await run_repo.list_recent_track_ids(station_id, max_runs=station.history_runs))
        if station.history_runs > 0 else set()
    )

    songs = await TrackPicker(llm, catalog, seed_builder).pick(
        music_source=station.music_source,
        target_minutes=station.length_minutes,
        exclude_ids=exclude_ids,
        source_artists=station.source_artists,
        source_albums=station.source_albums,
        source_tracks=station.source_tracks,
        discovery_rate=station.discovery_rate,
    )
    elapsed = _time.monotonic() - t0

    storefront = settings.musickit_storefront or "us"
    return TestPlaylistResponse(
        songs=[
            TestSongResponse(
                title=s.title,
                artist=s.artist,
                album=s.album,
                year=s.year,
                apple_music_id=s.apple_music_id,
                apple_music_url=(
                    f"https://music.apple.com/{storefront}/song/{s.apple_music_id}"
                    if s.apple_music_id else ""
                ),
                duration_secs=s.duration_secs,
            )
            for s in songs
        ],
        generation_secs=round(elapsed, 1),
    )


# ── Music Assistant playback ──────────────────────────────────────────


@app.post("/api/runs/{run_id}/play", response_model=MAPlayResponse)
async def play_run(
    run_id: str,
    body: MAPlayRequest = MAPlayRequest(),
    session: AsyncSession = Depends(get_session),
):
    repo = SqlPlaylistRunRepository(session)
    run = await repo.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if not run.playlist_json:
        raise HTTPException(400, "Run has no playlist yet")

    client = get_music_assistant_client()
    if client is None:
        raise HTTPException(503, "Music Assistant not configured (set MA_URL and MA_TOKEN)")

    player_id = body.player_id or settings.ma_default_player_id
    if not player_id:
        raise HTTPException(400, "No player_id supplied and no MA_DEFAULT_PLAYER_ID configured")

    if not settings.public_base_url:
        raise HTTPException(503, "PUBLIC_BASE_URL not configured (MA cannot reach our DJ audio)")

    base = settings.public_base_url.rstrip("/")
    station_repo = SqlStationRepository(session)
    station = await station_repo.get(run.station_id)
    dj_name = "?"
    art_url = None
    if station:
        if station.dj_id:
            dj_repo = SqlDJRepository(session)
            dj = await dj_repo.get(station.dj_id)
            if dj:
                dj_name = dj.name
        if station.album_art and (Path(settings.album_art_dir) / station.album_art).exists():
            art_url = f"{base}/api/stations/{station.id}/album-art"

    from rose_cinema.services.playlist_items import build_ma_media_items
    items = build_ma_media_items(
        run.playlist_json,
        station_name=station.name if station else "",
        dj_name=dj_name,
        base_url=base,
        art_url=art_url,
        episode=run.episode,
    )

    if not items:
        raise HTTPException(400, "No playable entries (no apple_music_ids and no DJ audio)")

    await client.play_media(player_id=player_id, media=items, option=body.option)
    # We return only URIs in the response for simplicity
    uris = [i if isinstance(i, str) else i["uri"] for i in items]
    return MAPlayResponse(player_id=player_id, queued=len(uris), uris=uris)


@app.post("/api/runs/{run_id}/save-to-ma", status_code=202)
async def save_run_to_ma(
    run_id: str,
    session: AsyncSession = Depends(get_session),
):
    from rose_cinema.models import PlaylistRun

    repo = SqlPlaylistRunRepository(session)
    run = await repo.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if not run.playlist_json:
        raise HTTPException(400, "Run has no playlist yet")
    if run.ma_playlist_id:
        return {"status": "already_saved", "playlist_id": run.ma_playlist_id}

    client = get_music_assistant_client()
    if client is None:
        raise HTTPException(503, "Music Assistant not configured")
    if not settings.public_base_url:
        raise HTTPException(503, "PUBLIC_BASE_URL not configured")

    run_obj = await session.get(PlaylistRun, run_id)
    if not run_obj:
        raise HTTPException(404, "Run not found")

    queue = EventQueue(async_session)
    await enqueue_ma_chain(session, queue, run_obj)
    await session.commit()

    return {"status": "queued", "run_id": run_id}


# ── Export / Import ───────────────────────────────────────────────


@app.get("/api/export", response_model=ExportData)
async def export_all(session: AsyncSession = Depends(get_session)):
    dj_repo = SqlDJRepository(session)
    station_repo = SqlStationRepository(session)
    all_djs = await dj_repo.list_all()
    all_stations = await station_repo.list_all()
    dj_map = {d.id: d.name for d in all_djs}
    return ExportData(
        djs=[
            DJExport(
                name=d.name, agent_md=d.agent_md,
                tts_provider=d.tts_provider, tts_voice_id=d.tts_voice_id,
                tts_voice_ref=d.tts_voice_ref,
            )
            for d in all_djs
        ],
        stations=[
            StationExport(
                name=s.name, description=s.description,
                length_minutes=s.length_minutes, dj_talk_rate=s.dj_talk_rate,
                dj_babble_rate=s.dj_babble_rate, dj_max_length_secs=s.dj_max_length_secs,
                max_playlists=s.max_playlists, music_source=s.music_source,
                source_artists=s.source_artists, source_albums=s.source_albums,
                source_tracks=s.source_tracks, cron_schedule=s.cron_schedule,
                discovery_rate=s.discovery_rate, history_runs=s.history_runs,
                dj_name=dj_map.get(s.dj_id) if s.dj_id else None,
                album_art=s.album_art,
                slug=s.slug,
            )
            for s in all_stations
        ],
    )


@app.post("/api/import", response_model=ImportResult)
async def import_all(body: ExportData, session: AsyncSession = Depends(get_session)):
    dj_repo = SqlDJRepository(session)
    station_repo = SqlStationRepository(session)

    existing_djs = await dj_repo.list_all()
    dj_name_map = {d.name: d.id for d in existing_djs}

    djs_created = 0
    djs_skipped = 0
    for dj_data in body.djs:
        if dj_data.name in dj_name_map:
            djs_skipped += 1
            continue
        record = await dj_repo.create(DJRecord(
            name=dj_data.name, agent_md=dj_data.agent_md,
            tts_provider=dj_data.tts_provider, tts_voice_id=dj_data.tts_voice_id,
            tts_voice_ref=dj_data.tts_voice_ref,
        ))
        dj_name_map[record.name] = record.id
        djs_created += 1

    stations_created = 0
    for s in body.stations:
        dj_id = dj_name_map.get(s.dj_name) if s.dj_name else None
        await station_repo.create(StationRecord(
            name=s.name, description=s.description,
            length_minutes=s.length_minutes, dj_talk_rate=s.dj_talk_rate,
            dj_babble_rate=s.dj_babble_rate, dj_max_length_secs=s.dj_max_length_secs,
            max_playlists=s.max_playlists, dj_id=dj_id, music_source=s.music_source,
            source_artists=s.source_artists, source_albums=s.source_albums,
            source_tracks=s.source_tracks, cron_schedule=s.cron_schedule,
            discovery_rate=s.discovery_rate, history_runs=s.history_runs,
        ))
        stations_created += 1

    return ImportResult(
        djs_created=djs_created,
        djs_skipped=djs_skipped,
        stations_created=stations_created,
    )


# ── Health ─────────────────────────────────────────────────────────────


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# ── Listen (personal Icecast stream) ──────────────────────────────────


@app.get("/listen/{station_slug}")
async def listen_stream(
    station_slug: str,
    uid: str = Query(..., min_length=1, max_length=200),
    session: AsyncSession = Depends(get_session),
):
    global _stream_manager
    if _stream_manager is None:
        raise HTTPException(503, "Stream manager not initialized")

    station_repo = SqlStationRepository(session)
    station = await station_repo.get_by_slug(station_slug)
    if not station:
        raise HTTPException(404, "Station not found")

    client = get_music_assistant_client()
    if client is None:
        raise HTTPException(503, "Music Assistant not configured (set MA_URL and MA_TOKEN)")
    if not settings.public_base_url:
        raise HTTPException(503, "PUBLIC_BASE_URL not configured")

    # resolve listen position
    pos_repo = SqlListenPositionRepository(session)
    today = date.today().isoformat()
    position = await pos_repo.get_position(station.id, uid, today)

    run_repo = SqlPlaylistRunRepository(session)
    runs = await run_repo.list_by_station(station.id)
    ready_runs = [r for r in runs if r.status in ("ready", "saved")]
    if not ready_runs:
        raise HTTPException(404, "No playlists available for this station")

    if position:
        run_id = position.run_id
        entry_index = position.entry_index
        # verify the run still exists
        run = await run_repo.get(run_id)
        if not run or run.status not in ("ready", "saved"):
            run_id = ready_runs[0].id
            entry_index = 0
    else:
        run_id = ready_runs[0].id
        entry_index = 0

    run = await run_repo.get(run_id)
    if not run:
        raise HTTPException(404, "Playlist run not found")

    # upsert initial position
    await pos_repo.upsert_position(ListenPositionRecord(
        station_id=station.id,
        uid=uid,
        run_id=run_id,
        entry_index=entry_index,
        listened_date=today,
    ))

    # resolve DJ info for media items
    base = settings.public_base_url.rstrip("/")
    dj_name = "?"
    art_url = None
    if station.dj_id:
        dj_repo = SqlDJRepository(session)
        dj = await dj_repo.get(station.dj_id)
        if dj:
            dj_name = dj.name
    if station.album_art and (Path(settings.album_art_dir) / station.album_art).exists():
        art_url = f"{base}/api/stations/{station.id}/album-art"

    # get or start stream
    stream = _stream_manager.get_stream(station.id, uid)
    if not stream:
        stream = await _stream_manager.start_stream(
            station_id=station.id,
            uid=uid,
            slug=station_slug,
            run_id=run_id,
            playlist_json=run.playlist_json,
            station_name=station.name,
            dj_name=dj_name,
            base_url=base,
            art_url=art_url,
            episode=run.episode,
            entry_index=entry_index,
        )

    icecast_url = f"http://{settings.icecast_host}:{settings.icecast_port}{stream.mount}"

    async def proxy_stream():
        _stream_manager.listener_connect(station.id, uid)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=300, write=10, pool=10)) as http:
                for attempt in range(40):
                    try:
                        async with http.stream("GET", icecast_url) as resp:
                            if resp.status_code != 200:
                                raise httpx.HTTPStatusError(
                                    f"Icecast returned {resp.status_code}",
                                    request=resp.request, response=resp,
                                )
                            async for chunk in resp.aiter_bytes(chunk_size=8192):
                                yield chunk
                            return
                    except (httpx.ConnectError, httpx.HTTPStatusError):
                        if attempt < 39:
                            await asyncio.sleep(0.5)
                        else:
                            raise
        finally:
            _stream_manager.listener_disconnect(station.id, uid)

    return StreamingResponse(
        proxy_stream(),
        media_type="audio/mpeg",
        headers={
            "icy-name": station.name,
            "Cache-Control": "no-cache, no-store",
            "Connection": "close",
        },
    )


@app.get("/listen-native/{station_slug}")
async def listen_native_stream(
    station_slug: str,
    request: Request,
    uid: str = Query(..., min_length=1, max_length=200),
    session: AsyncSession = Depends(get_session),
):
    station_repo = SqlStationRepository(session)
    station = await station_repo.get_by_slug(station_slug)
    if not station:
        raise HTTPException(404, "Station not found")

    pos_repo = SqlListenPositionRepository(session)
    today = date.today().isoformat()
    position = await pos_repo.get_position(station.id, uid, today)

    run_repo = SqlPlaylistRunRepository(session)
    runs = await run_repo.list_by_station(station.id)
    ready_runs = [r for r in runs if r.status in ("ready", "saved")]
    if not ready_runs:
        raise HTTPException(404, "No playlists available for this station")

    if position:
        run_id = position.run_id
        entry_index = position.entry_index
        run = await run_repo.get(run_id)
        if not run or run.status not in ("ready", "saved"):
            run_id = ready_runs[0].id
            entry_index = 0
    else:
        run_id = ready_runs[0].id
        entry_index = 0

    run = await run_repo.get(run_id)
    if not run:
        raise HTTPException(404, "Playlist run not found")

    entries = json.loads(run.playlist_json)

    await pos_repo.upsert_position(ListenPositionRecord(
        station_id=station.id,
        uid=uid,
        run_id=run_id,
        entry_index=entry_index,
        listened_date=today,
    ))

    if settings.apple_music_user_token:
        streamer = AppleMusicStreamer()
        cache = TrackCache(streamer)
        remaining = entries[entry_index:]
        uncached = [
            e for e in remaining
            if e.get("type") == "song"
            and e.get("apple_music_id")
            and not cache.is_cached(e["apple_music_id"])
        ]
        if uncached:
            await cache.ensure_playlist_cached(
                remaining, eager_count=3, max_downloads=3,
            )

            async def _bg_download(c=cache, r=remaining):
                try:
                    await c.ensure_playlist_cached(r)
                except Exception:
                    logger.exception("Background track download failed")

            asyncio.create_task(_bg_download())

    ez_session = await _ezstream_mgr.start_session(
        station_id=station.id,
        uid=uid,
        run_id=run_id,
        entries=entries,
        entry_index=entry_index,
        station_name=station.name,
    )

    want_icy = request.headers.get("icy-metadata") == "1"
    metaint = settings.native_stream_metaint
    session_key = f"{station.id}:{uid}"

    async def generate():
        if want_icy:
            async for chunk in ez_session.stream_with_icy(metaint):
                yield chunk
        else:
            async for chunk in ez_session.stream_plain():
                yield chunk

    headers = {
        "icy-name": station.name,
        "icy-br": settings.native_stream_bitrate.replace("k", ""),
        "Cache-Control": "no-cache, no-store",
        "Connection": "close",
        "Content-Length": str(2**63 - 1),
    }
    if want_icy:
        headers["icy-metaint"] = str(metaint)

    return StreamingResponse(
        generate(),
        media_type="audio/mpeg",
        headers=headers,
    )


@app.get("/listen-native/{station_slug}/now-playing")
async def native_now_playing(
    station_slug: str,
    uid: str = Query(..., min_length=1, max_length=200),
    session: AsyncSession = Depends(get_session),
):
    station_repo = SqlStationRepository(session)
    station = await station_repo.get_by_slug(station_slug)
    if not station:
        raise HTTPException(404, "Station not found")

    ez_session = _ezstream_mgr.get_session(station.id, uid) if _ezstream_mgr else None
    if not ez_session:
        raise HTTPException(404, "No active stream for this station/uid")

    entry = ez_session.current_entry
    return {
        "type": entry.get("type", ""),
        "title": entry.get("title", ""),
        "artist": entry.get("artist", ""),
        "album": entry.get("album", ""),
        "artwork_url": entry.get("artwork_url", ""),
        "entry_index": ez_session.entry_index,
        "total_entries": len(ez_session.entries),
    }


# ── Web UI (must be mounted last so it doesn't shadow /api routes) ────


_web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
_web_fallback = Path(__file__).resolve().parent.parent / "web"
_web_dir = _web_dist if _web_dist.is_dir() else _web_fallback
if _web_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_web_dir), html=True), name="web")
