from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from rose_cinema.config import settings
from rose_cinema.services.apple_music_stream import AppleMusicStreamer

logger = logging.getLogger(__name__)


class TrackCache:
    def __init__(self, streamer: AppleMusicStreamer) -> None:
        self._streamer = streamer
        self._dir = Path(settings.tracks_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._delay = settings.track_download_delay

    def path_for(self, apple_music_id: str) -> Path:
        return self._dir / f"{apple_music_id}.mp3"

    def is_cached(self, apple_music_id: str) -> bool:
        p = self.path_for(apple_music_id)
        return p.exists() and p.stat().st_size > 0

    async def download_track(self, apple_music_id: str) -> Path:
        dest = self.path_for(apple_music_id)
        if self.is_cached(apple_music_id):
            return dest

        info = await self._streamer.get_stream_info(apple_music_id)

        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y"]
        if info.is_encrypted and info.decryption_key:
            cmd += ["-decryption_key", info.decryption_key]
        cmd += ["-i", info.stream_url]
        cmd += [
            "-ar", "44100", "-ac", "2",
            "-c:a", "libmp3lame", "-b:a", settings.native_stream_bitrate,
            "-write_xing", "0", "-id3v2_version", "0",
        ]

        fd, tmp_path = tempfile.mkstemp(suffix=".mp3", dir=str(self._dir))
        os.close(fd)
        cmd += [tmp_path]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg failed ({proc.returncode}): {stderr.decode(errors='replace')[:500]}"
                )
            os.rename(tmp_path, str(dest))
            logger.info("Cached track %s → %s", apple_music_id, dest)
            return dest
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    async def ensure_playlist_cached(
        self,
        entries: list[dict],
        *,
        eager_count: int = 0,
        max_downloads: int = 0,
    ) -> dict[str, Path]:
        songs = [
            e for e in entries
            if e.get("type") == "song" and e.get("apple_music_id")
        ]
        cached: dict[str, Path] = {}
        downloaded = 0

        for i, song in enumerate(songs):
            aid = song["apple_music_id"]
            if self.is_cached(aid):
                cached[aid] = self.path_for(aid)
                continue
            if max_downloads and downloaded >= max_downloads:
                break
            try:
                path = await self.download_track(aid)
                cached[aid] = path
                downloaded += 1
                logger.info(
                    "Downloaded %d/%d: %s - %s",
                    i + 1, len(songs),
                    song.get("artist", "?"), song.get("title", "?"),
                )
            except Exception:
                logger.exception("Failed to download track %s", aid)
                continue

            if i >= eager_count:
                await asyncio.sleep(self._delay)

        return cached
