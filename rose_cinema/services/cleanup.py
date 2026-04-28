from __future__ import annotations

import json
import logging
from pathlib import Path

from rose_cinema.repositories import PlaylistRunRecord
from rose_cinema.repositories.sql import SqlPlaylistRunRepository
from rose_cinema.services.music_assistant import MusicAssistantClient

logger = logging.getLogger(__name__)


async def cleanup_run(
    run: PlaylistRunRecord,
    run_repo: SqlPlaylistRunRepository,
    audio_dir: str,
    ma_client: MusicAssistantClient | None = None,
) -> None:
    if run.ma_playlist_id and ma_client:
        try:
            await ma_client.remove_playlist(run.ma_playlist_id)
        except Exception:
            logger.warning(
                "Failed to remove MA playlist %s", run.ma_playlist_id, exc_info=True,
            )

    if run.playlist_json:
        for entry in json.loads(run.playlist_json):
            if entry.get("type") == "dj" and entry.get("audio_file"):
                path = Path(audio_dir) / Path(entry["audio_file"]).name
                if path.exists():
                    path.unlink()
                    logger.info("Deleted DJ audio: %s", path)

    await run_repo.delete(run.id)


async def trim_station_runs(
    station_id: str,
    max_playlists: int,
    run_repo: SqlPlaylistRunRepository,
    audio_dir: str,
    ma_client: MusicAssistantClient | None = None,
) -> None:
    if max_playlists <= 0:
        return
    runs = await run_repo.list_by_station(station_id)
    if len(runs) <= max_playlists:
        return
    for old_run in runs[max_playlists:]:
        await cleanup_run(old_run, run_repo, audio_dir, ma_client)
        logger.info("Trimmed old run %s for station %s", old_run.id, station_id)
