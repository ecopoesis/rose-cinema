from __future__ import annotations

import json
import logging

from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from rose_cinema.config import settings
from rose_cinema.database import get_session
from rose_cinema.repositories import DJRecord, StationRecord, PlaylistRunRecord
from rose_cinema.repositories.sqlite import (
    SqliteDJRepository,
    SqliteStationRepository,
    SqlitePlaylistRunRepository,
)
from rose_cinema.providers.factory import get_llm_provider
from rose_cinema.services.station_builder import StationBuilder, SongMetadata
from rose_cinema.services.track_picker import TrackPicker
from rose_cinema.services.musickit import get_music_catalog
from rose_cinema.services.music_assistant import get_music_assistant_client
from pathlib import Path
from rose_cinema.schemas import (
    DJCreate, DJUpdate, DJResponse,
    StationCreate, StationUpdate, StationResponse,
    GenerateRequest, PlaylistRunResponse, PlaylistEntryResponse,
    MAPlayRequest, MAPlayResponse,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Rose Cinema",
    description="AI-powered radio station generator for Apple Music",
    version="0.1.0",
)

# Serve DJ audio files
app.mount("/audio", StaticFiles(directory=settings.dj_audio_dir), name="audio")


# ── DJ endpoints ───────────────────────────────────────────────────────


@app.get("/api/djs", response_model=list[DJResponse])
async def list_djs(session: AsyncSession = Depends(get_session)):
    repo = SqliteDJRepository(session)
    records = await repo.list_all()
    return [DJResponse(**r.__dict__) for r in records]


@app.get("/api/djs/{dj_id}", response_model=DJResponse)
async def get_dj(dj_id: str, session: AsyncSession = Depends(get_session)):
    repo = SqliteDJRepository(session)
    record = await repo.get(dj_id)
    if not record:
        raise HTTPException(404, "DJ not found")
    return DJResponse(**record.__dict__)


@app.post("/api/djs", response_model=DJResponse, status_code=201)
async def create_dj(body: DJCreate, session: AsyncSession = Depends(get_session)):
    repo = SqliteDJRepository(session)
    record = await repo.create(DJRecord(
        name=body.name,
        agent_md=body.agent_md,
        tts_provider=body.tts_provider,
        tts_voice_id=body.tts_voice_id,
    ))
    return DJResponse(**record.__dict__)


@app.put("/api/djs/{dj_id}", response_model=DJResponse)
async def update_dj(
    dj_id: str, body: DJUpdate, session: AsyncSession = Depends(get_session)
):
    repo = SqliteDJRepository(session)
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

    updated = await repo.update(existing)
    return DJResponse(**updated.__dict__)


@app.delete("/api/djs/{dj_id}", status_code=204)
async def delete_dj(dj_id: str, session: AsyncSession = Depends(get_session)):
    repo = SqliteDJRepository(session)
    if not await repo.delete(dj_id):
        raise HTTPException(404, "DJ not found")


# ── Station endpoints ──────────────────────────────────────────────────


@app.get("/api/stations", response_model=list[StationResponse])
async def list_stations(session: AsyncSession = Depends(get_session)):
    repo = SqliteStationRepository(session)
    records = await repo.list_all()
    return [StationResponse(**r.__dict__) for r in records]


@app.get("/api/stations/{station_id}", response_model=StationResponse)
async def get_station(station_id: str, session: AsyncSession = Depends(get_session)):
    repo = SqliteStationRepository(session)
    record = await repo.get(station_id)
    if not record:
        raise HTTPException(404, "Station not found")
    return StationResponse(**record.__dict__)


@app.post("/api/stations", response_model=StationResponse, status_code=201)
async def create_station(
    body: StationCreate, session: AsyncSession = Depends(get_session)
):
    repo = SqliteStationRepository(session)
    record = await repo.create(StationRecord(
        name=body.name,
        description=body.description,
        length_minutes=body.length_minutes,
        dj_talk_rate=body.dj_talk_rate,
        dj_babble_rate=body.dj_babble_rate,
        dj_max_length_secs=body.dj_max_length_secs,
        dj_id=body.dj_id,
        music_source=body.music_source,
    ))
    return StationResponse(**record.__dict__)


@app.put("/api/stations/{station_id}", response_model=StationResponse)
async def update_station(
    station_id: str, body: StationUpdate, session: AsyncSession = Depends(get_session)
):
    repo = SqliteStationRepository(session)
    existing = await repo.get(station_id)
    if not existing:
        raise HTTPException(404, "Station not found")

    for field_name in (
        "name", "description", "length_minutes", "dj_talk_rate",
        "dj_babble_rate", "dj_max_length_secs", "dj_id", "music_source",
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
    repo = SqliteStationRepository(session)
    if not await repo.delete(station_id):
        raise HTTPException(404, "Station not found")


# ── Playlist generation ───────────────────────────────────────────────


@app.post("/api/generate", response_model=PlaylistRunResponse)
async def generate_playlist(
    body: GenerateRequest, session: AsyncSession = Depends(get_session)
):
    station_repo = SqliteStationRepository(session)
    dj_repo = SqliteDJRepository(session)
    run_repo = SqlitePlaylistRunRepository(session)

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

    try:
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
            if catalog is None:
                logger.warning("MusicKit catalog not configured — track verification disabled")
            songs = await TrackPicker(llm, catalog).pick(
                music_source=station.music_source,
                target_minutes=station.length_minutes,
            )
            logger.info(
                "TrackPicker selected %d songs for station '%s'",
                len(songs), station.name,
            )

        entries = await builder.build_playlist(station, dj, songs)

        run.status = "ready"
        run.playlist_json = json.dumps([e.to_dict() for e in entries])
        await run_repo.update(run)

        return PlaylistRunResponse(
            id=run.id,
            station_id=run.station_id,
            status=run.status,
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
    repo = SqlitePlaylistRunRepository(session)
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
        entries=entries,
        error_message=run.error_message,
    )


# ── Music Assistant playback ──────────────────────────────────────────


@app.post("/api/runs/{run_id}/play", response_model=MAPlayResponse)
async def play_run(
    run_id: str,
    body: MAPlayRequest = MAPlayRequest(),
    session: AsyncSession = Depends(get_session),
):
    repo = SqlitePlaylistRunRepository(session)
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
    uris: list[str] = []
    for e in json.loads(run.playlist_json):
        if e.get("type") == "song" and e.get("apple_music_id"):
            uris.append(f"apple_music://track/{e['apple_music_id']}")
        elif e.get("type") == "dj" and e.get("audio_file"):
            uris.append(f"{base}/audio/{Path(e['audio_file']).name}")
        else:
            logger.warning("Skipping unplayable entry: type=%s id=%s file=%s",
                           e.get("type"), e.get("apple_music_id"), e.get("audio_file"))

    if not uris:
        raise HTTPException(400, "No playable entries (no apple_music_ids and no DJ audio)")

    await client.play_media(player_id=player_id, media_uris=uris, option=body.option)
    return MAPlayResponse(player_id=player_id, queued=len(uris), uris=uris)


# ── Health ─────────────────────────────────────────────────────────────


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# ── Web UI (must be mounted last so it doesn't shadow /api routes) ────


_web_dir = Path(__file__).resolve().parent.parent / "web"
if _web_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_web_dir), html=True), name="web")
