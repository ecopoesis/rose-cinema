from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Awaitable

from rose_cinema.config import settings
from rose_cinema.providers.factory import get_llm_provider, get_tts_provider
from rose_cinema.repositories import DJRecord, PlaylistRunRecord
from rose_cinema.services.station_builder import SongMetadata, PlaylistEntry, _tag_dj_audio

logger = logging.getLogger(__name__)


async def _load_dj(dj_id: str) -> DJRecord:
    from rose_cinema.database import async_session
    from rose_cinema.repositories.sql import SqlDJRepository

    async with async_session() as session:
        repo = SqlDJRepository(session)
        dj = await repo.get(dj_id)
        if not dj:
            raise RuntimeError(f"DJ {dj_id} not found")
        return dj


async def _load_run(run_id: str) -> PlaylistRunRecord:
    from rose_cinema.database import async_session
    from rose_cinema.repositories.sql import SqlPlaylistRunRepository

    async with async_session() as session:
        repo = SqlPlaylistRunRepository(session)
        run = await repo.get(run_id)
        if not run:
            raise RuntimeError(f"Run {run_id} not found")
        return run


async def handle_pick_tracks(payload: dict) -> dict:
    songs_override = payload.get("songs_override")
    if songs_override:
        return {"songs": songs_override}

    station_id = payload["station_id"]
    music_source = payload["music_source"]
    length_minutes = payload["length_minutes"]
    exclude_ids = set(payload.get("exclude_ids", []))

    from rose_cinema.services.track_picker import TrackPicker
    from rose_cinema.services.seed_pool import SeedPoolBuilder
    from rose_cinema.services.musickit import get_music_catalog
    from rose_cinema.services.musicbrainz import get_musicbrainz_client

    llm = get_llm_provider()
    catalog = get_music_catalog()
    mb_client = get_musicbrainz_client()
    seed_builder = SeedPoolBuilder(catalog, llm, mb_client) if catalog else None

    songs = await TrackPicker(llm, catalog, seed_builder).pick(
        music_source=music_source,
        target_minutes=length_minutes,
        exclude_ids=exclude_ids,
        source_artists=payload.get("source_artists"),
        source_albums=payload.get("source_albums"),
        source_tracks=payload.get("source_tracks"),
        discovery_rate=payload.get("discovery_rate", 0.5),
    )
    logger.info("pick_tracks: selected %d songs for station %s", len(songs), station_id[:8])
    return {
        "songs": [
            {
                "title": s.title,
                "artist": s.artist,
                "album": s.album,
                "year": s.year,
                "apple_music_id": s.apple_music_id,
                "duration_secs": s.duration_secs,
                "track_number": s.track_number,
            }
            for s in songs
        ]
    }


async def handle_generate_intro_script(payload: dict) -> dict:
    dj = await _load_dj(payload["dj_id"])

    from rose_cinema.services.dj_script import DJScriptService

    llm = get_llm_provider()
    service = DJScriptService(llm)
    script = await service.generate_intro(
        dj=dj,
        station_name=payload["station_name"],
        babble_rate=payload["babble_rate"],
        max_seconds=payload["max_secs"],
    )
    return {"script": script}


async def handle_synthesize_intro(payload: dict) -> dict:
    tts = get_tts_provider(payload["tts_provider"])
    run_prefix = payload["run_prefix"]
    station_name = payload.get("station_name", "")
    episode = payload.get("episode")

    audio_path = await tts.synthesize(
        text=payload["script"],
        voice_id=payload["tts_voice_id"],
        output_path=Path(settings.dj_audio_dir) / f"{run_prefix}_intro",
        reference_audio=payload.get("tts_voice_ref", ""),
    )

    title = f"{station_name} - Episode {episode} - Intro" if episode else f"{station_name} - Intro ({run_prefix})"
    _tag_dj_audio(audio_path, title, (await _load_dj(payload["dj_id"])).name)

    return {"audio_file": str(audio_path)}


async def handle_generate_transition(payload: dict) -> dict:
    dj = await _load_dj(payload["dj_id"])

    from rose_cinema.services.dj_script import DJScriptService

    llm = get_llm_provider()
    service = DJScriptService(llm)
    script = await service.generate_transition(
        dj=dj,
        previous_song=payload.get("previous_song"),
        next_song=payload["next_song"],
        babble_rate=payload["babble_rate"],
        max_seconds=payload["max_secs"],
    )
    return {"script": script, "song_index": payload.get("song_index", 0)}


async def handle_synthesize_transition(payload: dict) -> dict:
    tts = get_tts_provider(payload["tts_provider"])
    run_prefix = payload["run_prefix"]
    station_name = payload.get("station_name", "")
    episode = payload.get("episode")
    segment_number = payload.get("segment_number", 0)
    song_index = payload.get("song_index", 0)

    audio_path = await tts.synthesize(
        text=payload["script"],
        voice_id=payload["tts_voice_id"],
        output_path=Path(settings.dj_audio_dir) / f"{run_prefix}_seg{song_index:03d}",
        reference_audio=payload.get("tts_voice_ref", ""),
    )

    title = f"{station_name} - Episode {episode} - Part {segment_number}" if episode else f"{station_name} - DJ {segment_number} ({run_prefix})"
    _tag_dj_audio(audio_path, title, (await _load_dj(payload["dj_id"])).name)

    return {
        "audio_file": str(audio_path),
        "song_index": song_index,
        "segment_number": segment_number,
    }


async def handle_finalize_playlist(payload: dict) -> dict:
    import time
    from rose_cinema.database import async_session
    from rose_cinema.models import GenerationEvent, PlaylistRun
    from sqlalchemy import select

    run_id = payload["run_id"]

    async with async_session() as session:
        run_obj = await session.get(PlaylistRun, run_id)
        if not run_obj:
            raise RuntimeError(f"Run {run_id} not found")

        pick_result = await session.execute(
            select(GenerationEvent)
            .where(
                GenerationEvent.run_id == run_id,
                GenerationEvent.step_type == "pick_tracks",
                GenerationEvent.status == "completed",
            )
        )
        pick_event = pick_result.scalar_one_or_none()
        if not pick_event:
            raise RuntimeError("No completed pick_tracks event")

        songs = pick_event.result.get("songs", [])

        synth_intros = await session.execute(
            select(GenerationEvent)
            .where(
                GenerationEvent.run_id == run_id,
                GenerationEvent.step_type == "synthesize_intro",
                GenerationEvent.status == "completed",
            )
        )
        intro_event = synth_intros.scalar_one_or_none()

        synth_transitions = await session.execute(
            select(GenerationEvent)
            .where(
                GenerationEvent.run_id == run_id,
                GenerationEvent.step_type == "synthesize_transition",
                GenerationEvent.status == "completed",
            )
            .order_by(GenerationEvent.step_index)
        )
        transition_events = {
            (e.result or {}).get("song_index", e.step_index): e
            for e in synth_transitions.scalars().all()
        }

        gen_transitions = await session.execute(
            select(GenerationEvent)
            .where(
                GenerationEvent.run_id == run_id,
                GenerationEvent.step_type == "generate_transition",
                GenerationEvent.status == "completed",
            )
        )
        script_by_index = {
            (e.result or {}).get("song_index", e.step_index): (e.result or {}).get("script", "")
            for e in gen_transitions.scalars().all()
        }

        entries: list[dict] = []

        if intro_event:
            intro_result = intro_event.result or {}
            intro_payload = intro_event.payload or {}
            entries.append(PlaylistEntry(
                type="dj",
                script=intro_payload.get("script", ""),
                audio_file=intro_result.get("audio_file", ""),
            ).to_dict())

        segment_num = 0
        for i, song in enumerate(songs):
            if i in transition_events:
                t_result = transition_events[i].result or {}
                segment_num += 1
                entries.append(PlaylistEntry(
                    type="dj",
                    script=script_by_index.get(i, ""),
                    audio_file=t_result.get("audio_file", ""),
                ).to_dict())

            entries.append(PlaylistEntry(
                type="song",
                title=song.get("title", ""),
                artist=song.get("artist", ""),
                album=song.get("album", ""),
                year=song.get("year", ""),
                apple_music_id=song.get("apple_music_id", ""),
                duration_secs=song.get("duration_secs", 210.0),
            ).to_dict())

        run_obj.playlist_json = json.dumps(entries)
        run_obj.status = "ready"
        song_count = sum(1 for e in entries if e.get("type") == "song")
        dj_count = sum(1 for e in entries if e.get("type") == "dj")
        logger.info(
            "[%s] Finalized playlist: %d entries (%d songs, %d DJ)",
            run_id[:8], len(entries), song_count, dj_count,
        )
        await session.commit()

    return {"entry_count": len(entries), "song_count": song_count, "dj_count": dj_count}


async def handle_ma_ingest_dj(payload: dict) -> dict:
    from rose_cinema.services.music_assistant import get_music_assistant_client

    client = get_music_assistant_client()
    if not client:
        raise RuntimeError("Music Assistant not configured")

    audio_url = payload["audio_url"]
    segment_name = payload["segment_name"]
    dj_name = payload["dj_name"]
    art_url = payload.get("art_url")

    import websockets
    async with client._session() as (ws, mid):
        from rose_cinema.services.music_assistant import _call

        add_item = {
            "uri": audio_url,
            "item_id": audio_url,
            "provider": "builtin",
            "media_type": "track",
            "name": segment_name,
            "artists": [{
                "name": dj_name,
                "item_id": dj_name,
                "provider": "builtin",
                "media_type": "artist",
            }],
            "metadata": {},
        }
        r = await _call(ws, mid, "music/library/add_item", item=add_item)
        result = r.get("result") or {}
        lib_uri = result.get("uri")
        if not lib_uri:
            raise RuntimeError(f"MA add_item returned no URI: {r!r}")

        if art_url:
            track_id = result.get("item_id")
            if track_id:
                try:
                    r_upd = await _call(
                        ws, mid, "music/tracks/update",
                        item_id=str(track_id),
                        update={
                            "item_id": str(track_id),
                            "provider": "library",
                            "media_type": "track",
                            "name": result.get("name", ""),
                            "uri": lib_uri,
                            "provider_mappings": [{
                                "item_id": audio_url,
                                "provider_domain": "builtin",
                                "provider_instance": "builtin",
                                "available": True,
                            }],
                            "metadata": {
                                "images": [{
                                    "type": "thumb",
                                    "path": art_url,
                                    "provider": "builtin",
                                    "remotely_accessible": True,
                                }]
                            },
                        },
                    )
                    if r_upd.get("error"):
                        logger.warning("MA artwork update failed: %s", r_upd["error"])
                except Exception:
                    logger.warning("Failed to set artwork for %s", audio_url)

    return {"url": audio_url, "library_uri": lib_uri}


async def handle_ma_create_playlist(payload: dict) -> dict:
    from rose_cinema.database import async_session
    from rose_cinema.models import PlaylistRun, Station
    from rose_cinema.services.music_assistant import get_music_assistant_client, _call

    run_id = payload["run_id"]

    async with async_session() as session:
        run = await session.get(PlaylistRun, run_id)
        if not run:
            raise RuntimeError(f"Run {run_id} not found")

        if run.ma_playlist_id:
            return {"playlist_id": run.ma_playlist_id, "skipped": True}

        station = await session.get(Station, run.station_id)
        station_name = station.name if station else "Unknown"
        ep = run.episode

    if ep:
        name = f"{station_name} Episode {ep} ({datetime.now().strftime('%Y-%m-%d')})"
    else:
        name = f"{station_name} — {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    client = get_music_assistant_client()
    if not client:
        raise RuntimeError("Music Assistant not configured")

    async with client._session() as (ws, mid):
        r = await _call(ws, mid, "music/playlists/create_playlist",
                        name=name, provider_instance_or_domain="builtin")
        pl = r.get("result") or {}
        pid = pl.get("item_id")
        if not pid:
            raise RuntimeError(f"MA create_playlist returned no item_id: {r!r}")

    async with async_session() as session:
        run = await session.get(PlaylistRun, run_id)
        if run:
            run.ma_playlist_id = pid
            await session.commit()

    return {
        "playlist_id": pid,
        "playlist_uri": pl.get("uri"),
        "playlist_name": pl.get("name"),
    }


async def handle_ma_add_tracks(payload: dict) -> dict:
    from rose_cinema.services.music_assistant import get_music_assistant_client, _call

    playlist_id = payload["playlist_id"]
    ordered_uris = payload["ordered_uris"]

    client = get_music_assistant_client()
    if not client:
        raise RuntimeError("Music Assistant not configured")

    async with client._session() as (ws, mid):
        r = await _call(ws, mid, "music/playlists/add_playlist_tracks",
                        db_playlist_id=playlist_id, uris=ordered_uris)

    return {"queued_count": len(ordered_uris)}


async def handle_download_tracks(payload: dict) -> dict:
    run = await _load_run(payload["run_id"])
    entries = json.loads(run.playlist_json) if run.playlist_json else []
    songs = [e for e in entries if e.get("type") == "song" and e.get("apple_music_id")]
    if not songs:
        return {"downloaded": 0, "skipped": 0, "failed": 0}

    from rose_cinema.database import async_session
    from rose_cinema.services.apple_music_stream import AppleMusicStreamer
    from rose_cinema.services.track_cache import TrackCache

    streamer = AppleMusicStreamer()
    cache = TrackCache(streamer, async_session)
    cached = await cache.ensure_playlist_cached(entries)

    downloaded = len(cached)
    failed = len(songs) - downloaded
    logger.info(
        "download_tracks: %d downloaded, %d failed for run %s",
        downloaded, failed, payload["run_id"][:8],
    )
    return {"downloaded": downloaded, "failed": failed}


async def handle_ma_cleanup(payload: dict) -> dict:
    from rose_cinema.database import async_session
    from rose_cinema.repositories.sql import SqlPlaylistRunRepository
    from rose_cinema.services.cleanup import trim_station_runs
    from rose_cinema.services.music_assistant import get_music_assistant_client

    station_id = payload["station_id"]
    max_playlists = payload["max_playlists"]
    run_id = payload["run_id"]

    async with async_session() as session:
        run_repo = SqlPlaylistRunRepository(session)
        ma_client = get_music_assistant_client()
        await trim_station_runs(
            station_id, max_playlists, run_repo, settings.dj_audio_dir, ma_client,
        )
        await session.commit()

    return {"trimmed": True}


async def handle_cache_tracks(payload: dict) -> dict:
    """Download-only: cache tracks as .m4a without triggering the MA chain."""
    run = await _load_run(payload["run_id"])
    entries = json.loads(run.playlist_json) if run.playlist_json else []
    songs = [e for e in entries if e.get("type") == "song" and e.get("apple_music_id")]
    if not songs:
        return {"downloaded": 0, "failed": 0}

    from rose_cinema.database import async_session
    from rose_cinema.services.apple_music_stream import AppleMusicStreamer
    from rose_cinema.services.track_cache import TrackCache

    streamer = AppleMusicStreamer()
    cache = TrackCache(streamer, async_session)
    cached = await cache.ensure_playlist_cached(entries)

    downloaded = len(cached)
    failed = len(songs) - downloaded
    logger.info(
        "cache_tracks: %d downloaded, %d failed for run %s",
        downloaded, failed, payload["run_id"][:8],
    )
    return {"downloaded": downloaded, "failed": failed}


HANDLERS: dict[str, Callable[[dict], Awaitable[dict]]] = {
    "pick_tracks": handle_pick_tracks,
    "generate_intro_script": handle_generate_intro_script,
    "synthesize_intro": handle_synthesize_intro,
    "generate_transition": handle_generate_transition,
    "synthesize_transition": handle_synthesize_transition,
    "finalize_playlist": handle_finalize_playlist,
    "ma_ingest_dj": handle_ma_ingest_dj,
    "ma_create_playlist": handle_ma_create_playlist,
    "ma_add_tracks": handle_ma_add_tracks,
    "ma_cleanup": handle_ma_cleanup,
    "download_tracks": handle_download_tracks,
    "cache_tracks": handle_cache_tracks,
}
