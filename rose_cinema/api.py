from __future__ import annotations

import json
import logging
import time

from fastapi import FastAPI, Depends, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from rose_cinema.config import settings
from rose_cinema.database import get_session
from rose_cinema.repositories import DJRecord, StationRecord, PlaylistRunRecord
from rose_cinema.repositories.sql import (
    SqlDJRepository,
    SqlStationRepository,
    SqlPlaylistRunRepository,
)
from rose_cinema.providers.factory import get_llm_provider
from rose_cinema.services.station_builder import StationBuilder, SongMetadata
from rose_cinema.services.track_picker import TrackPicker
from rose_cinema.services.musickit import get_music_catalog
from rose_cinema.services.seed_pool import SeedPoolBuilder
from rose_cinema.services.music_assistant import get_music_assistant_client
from pathlib import Path
from rose_cinema.schemas import (
    DJCreate, DJUpdate, DJResponse,
    StationCreate, StationUpdate, StationResponse,
    GenerateRequest, PlaylistRunResponse, PlaylistEntryResponse,
    PlaylistRunSummary,
    MAPlayRequest, MAPlayResponse, MASaveResponse,
    DJExport, StationExport, ExportData, ImportResult,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Rose Cinema",
    description="AI-powered radio station generator for Apple Music",
    version="0.1.0",
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

    # Create a run record
    run = await run_repo.create(PlaylistRunRecord(station_id=station.id, status="generating"))

    t0 = time.monotonic()
    try:
        logger.info("[%s] Starting generation for station '%s' (DJ: %s)", station.id[:8], station.name, dj.name)
        llm = get_llm_provider()
        builder = StationBuilder(llm, audio_dir=settings.dj_audio_dir)

        if body.songs:
            songs = [
                SongMetadata(
                    title=s.title,
                    artist=s.artist,
                    album=s.album,
                    year=s.year,
                    apple_music_id=s.apple_music_id,
                    duration_secs=s.duration_secs,
                )
                for s in body.songs
            ]
        else:
            if not station.music_source:
                raise HTTPException(
                    400, "Station has no music_source and no songs were provided"
                )
            catalog = get_music_catalog()
            seed_builder = SeedPoolBuilder(catalog, llm) if catalog else None
            if catalog is None:
                logger.warning("MusicKit catalog not configured — pool grounding disabled, falling back to LLM-discovery")
            exclude_ids = await run_repo.list_recent_track_ids(station.id, max_runs=3)
            logger.info("[%s] Picking tracks for %d minutes from source: %s (excluding %d recent)", station.name, station.length_minutes, station.music_source[:80], len(exclude_ids))
            songs = await TrackPicker(llm, catalog, seed_builder).pick(
                music_source=station.music_source,
                target_minutes=station.length_minutes,
                exclude_ids=exclude_ids,
            )
            logger.info(
                "TrackPicker selected %d songs for station '%s'",
                len(songs), station.name,
            )

        logger.info("[%s] Building playlist with %d songs…", station.name, len(songs))
        entries = await builder.build_playlist(station, dj, songs, episode=run.episode)

        run.status = "ready"
        elapsed = round(time.monotonic() - t0, 1)
        run.generation_secs = elapsed
        logger.info("[%s] Generation complete — %d entries in %.1fs", station.name, len(entries), elapsed)
        run.playlist_json = json.dumps([e.to_dict() for e in entries])
        await run_repo.update(run)

        return PlaylistRunResponse(
            id=run.id,
            station_id=run.station_id,
            status=run.status,
            episode=run.episode,
            entries=[PlaylistEntryResponse(**e.to_dict()) for e in entries],
        )

    except Exception as exc:
        logger.exception("Playlist generation failed")
        run.status = "failed"
        run.error_message = str(exc)
        await run_repo.update(run)
        raise HTTPException(500, f"Generation failed: {exc}")


@app.get("/api/runs/{run_id}", response_model=PlaylistRunResponse)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)):
    repo = SqlPlaylistRunRepository(session)
    run = await repo.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    entries = []
    if run.playlist_json:
        entries = [PlaylistEntryResponse(**e) for e in json.loads(run.playlist_json)]

    return PlaylistRunResponse(
        id=run.id,
        station_id=run.station_id,
        status=run.status,
        episode=run.episode,
        entries=entries,
        error_message=run.error_message,
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


@app.delete("/api/runs/{run_id}", status_code=204)
async def delete_run(run_id: str, session: AsyncSession = Depends(get_session)):
    run_repo = SqlPlaylistRunRepository(session)
    run = await run_repo.get(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    from rose_cinema.services.cleanup import cleanup_run
    ma_client = get_music_assistant_client()
    await cleanup_run(run, run_repo, settings.dj_audio_dir, ma_client)


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

    ep = run.episode
    items: list[str | dict] = []
    dj_part = 0
    for e in json.loads(run.playlist_json):
        if e.get("type") == "song" and e.get("apple_music_id"):
            items.append(f"apple_music://track/{e['apple_music_id']}")
        elif e.get("type") == "dj" and e.get("audio_file"):
            fname = Path(e['audio_file']).name
            url = f"{base}/audio/{fname}"
            if ep and station:
                seg_name = f"{station.name} - Episode {ep} - Intro" if dj_part == 0 else f"{station.name} - Episode {ep} - Part {dj_part}"
            else:
                seg_name = f"{station.name} - DJ {dj_part}" if station else f"DJ Segment {dj_part}"
            dj_part += 1
            item = {
                "uri": url,
                "item_id": url,
                "provider": "builtin",
                "media_type": "track",
                "name": seg_name,
                "artists": [{
                    "name": dj_name,
                    "item_id": dj_name,
                    "provider": "builtin",
                    "media_type": "artist",
                }],
                "metadata": {}
            }
            if art_url:
                item["metadata"]["images"] = [{"type": "thumb", "path": art_url, "provider": "builtin", "remotely_accessible": True}]
            items.append(item)
        else:
            logger.warning("Skipping unplayable entry: type=%s id=%s file=%s",
                           e.get("type"), e.get("apple_music_id"), e.get("audio_file"))

    if not items:
        raise HTTPException(400, "No playable entries (no apple_music_ids and no DJ audio)")

    await client.play_media(player_id=player_id, media=items, option=body.option)
    # We return only URIs in the response for simplicity
    uris = [i if isinstance(i, str) else i["uri"] for i in items]
    return MAPlayResponse(player_id=player_id, queued=len(uris), uris=uris)


@app.post("/api/runs/{run_id}/save-to-ma", response_model=MASaveResponse)
async def save_run_to_ma(
    run_id: str,
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
        raise HTTPException(503, "Music Assistant not configured")
    if not settings.public_base_url:
        raise HTTPException(503, "PUBLIC_BASE_URL not configured")

    # Look up the station and DJ for naming and metadata
    station_repo = SqlStationRepository(session)
    station = await station_repo.get(run.station_id)
    station_name = station.name if station else "Unknown station"
    dj_name = "?"
    art_url = None
    if station:
        if station.dj_id:
            dj_repo = SqlDJRepository(session)
            dj = await dj_repo.get(station.dj_id)
            if dj:
                dj_name = dj.name
        if station.album_art and (Path(settings.album_art_dir) / station.album_art).exists():
            art_url = f"{settings.public_base_url.rstrip('/')}/api/stations/{station.id}/album-art"

    base = settings.public_base_url.rstrip("/")
    ep = run.episode
    ordered: list[str] = []
    dj_metadata: list[dict] = []
    apple_uris: list[str] = []
    dj_part = 0
    for e in json.loads(run.playlist_json):
        if e.get("type") == "song" and e.get("apple_music_id"):
            uri = f"apple_music://track/{e['apple_music_id']}"
            ordered.append(uri); apple_uris.append(uri)
        elif e.get("type") == "dj" and e.get("audio_file"):
            fname = Path(e['audio_file']).name
            url = f"{base}/audio/{fname}"
            ordered.append(url)
            if ep:
                seg_name = f"{station_name} - Episode {ep} - Intro" if dj_part == 0 else f"{station_name} - Episode {ep} - Part {dj_part}"
            else:
                seg_name = f"{station_name} - DJ {dj_part}"
            dj_part += 1
            meta = {
                "uri": url,
                "item_id": url,
                "provider": "builtin",
                "media_type": "track",
                "name": seg_name,
                "artists": [{
                    "name": dj_name,
                    "item_id": dj_name,
                    "provider": "builtin",
                    "media_type": "artist",
                }],
                "metadata": {}
            }
            if art_url:
                meta["metadata"]["images"] = [{"type": "thumb", "path": art_url, "provider": "builtin", "remotely_accessible": True}]
            dj_metadata.append(meta)

    if not ordered:
        raise HTTPException(400, "Run has no playable entries")

    from datetime import datetime
    if ep:
        name = f"{station_name} Episode {ep} ({datetime.now().strftime('%Y-%m-%d')})"
    else:
        name = f"{station_name} — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    out = await client.save_as_playlist(
        name=name,
        apple_music_uris=apple_uris,
        dj_mp3_metadata=dj_metadata,
        ordered_uris=ordered,
        art_url=art_url,
    )

    run.ma_playlist_id = out["playlist_id"]
    await repo.update(run)

    if station and station.max_playlists > 0:
        from rose_cinema.services.cleanup import trim_station_runs
        await trim_station_runs(
            station_id=run.station_id,
            max_playlists=station.max_playlists,
            run_repo=repo,
            audio_dir=settings.dj_audio_dir,
            ma_client=client,
        )

    return MASaveResponse(**out)


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
                dj_name=dj_map.get(s.dj_id) if s.dj_id else None,
                album_art=s.album_art,
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


# ── Web UI (must be mounted last so it doesn't shadow /api routes) ────


_web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
_web_fallback = Path(__file__).resolve().parent.parent / "web"
_web_dir = _web_dist if _web_dist.is_dir() else _web_fallback
if _web_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_web_dir), html=True), name="web")
