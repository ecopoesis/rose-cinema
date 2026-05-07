from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
from pathlib import Path

import httpx
from mutagen.mp3 import MP3

from rose_cinema.config import settings

logger = logging.getLogger(__name__)


class EzstreamSession:
    def __init__(
        self,
        station_id: str,
        uid: str,
        run_id: str,
        entries: list[dict],
        entry_index: int,
        station_name: str,
        mount: str,
    ) -> None:
        self.station_id = station_id
        self.uid = uid
        self.run_id = run_id
        self.entries = entries
        self.entry_index = entry_index
        self.station_name = station_name
        self.mount = mount
        self._proc: asyncio.subprocess.Process | None = None
        self._meta_task: asyncio.Task | None = None
        self._work_dir: Path | None = None
        self._current_entry_idx = entry_index

    @property
    def current_entry(self) -> dict:
        if 0 <= self._current_entry_idx < len(self.entries):
            return self.entries[self._current_entry_idx]
        return {}

    def _build_concat_and_schedule(
        self, work_dir: Path,
    ) -> tuple[Path, list[tuple[float, int]]]:
        concat_file = work_dir / "concat.txt"
        tracks_dir = Path(settings.tracks_dir)
        lines: list[str] = []
        schedule: list[tuple[float, int]] = []
        cumulative = 0.0

        for i, entry in enumerate(
            self.entries[self.entry_index:], start=self.entry_index,
        ):
            if entry.get("type") == "dj":
                audio_file = entry.get("audio_file", "")
                if not audio_file or not Path(audio_file).exists():
                    continue
                path = Path(audio_file).resolve()
            elif entry.get("type") == "song":
                aid = entry.get("apple_music_id", "")
                path = tracks_dir / f"{aid}.mp3"
                if not path.exists():
                    logger.warning("Track not cached, skipping: %s", aid)
                    continue
                path = path.resolve()
            else:
                continue

            escaped = str(path).replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
            schedule.append((cumulative, i))

            try:
                duration = MP3(str(path)).info.length
            except Exception:
                duration = entry.get("duration_secs") or 30.0
            cumulative += duration

        concat_file.write_text("\n".join(lines) + "\n")
        return concat_file, schedule

    async def start(self) -> None:
        work_dir = Path(settings.streams_dir) / f"{self.station_id}_{self.uid}"
        work_dir.mkdir(parents=True, exist_ok=True)
        self._work_dir = work_dir

        concat_file, schedule = self._build_concat_and_schedule(work_dir)

        password = urllib.parse.quote(settings.icecast_source_password, safe="")
        icecast_url = (
            f"icecast://source:{password}"
            f"@{settings.icecast_host}:{settings.icecast_port}{self.mount}"
        )

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-re",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-ar", "44100", "-ac", "2",
            "-c:a", "libmp3lame", "-b:a", settings.native_stream_bitrate,
            "-write_xing", "0", "-reservoir", "0", "-id3v2_version", "0",
            "-flush_packets", "1",
            "-f", "mp3",
            "-content_type", "audio/mpeg",
            "-ice_name", self.station_name,
            icecast_url,
        ]

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info(
            "Started ffmpeg→icecast pid=%d mount=%s for %s:%s",
            self._proc.pid, self.mount, self.station_id[:8], self.uid,
        )

        if schedule:
            self._meta_task = asyncio.create_task(
                self._update_metadata_loop(schedule),
            )

    async def _update_metadata_loop(
        self, schedule: list[tuple[float, int]],
    ) -> None:
        t0 = time.monotonic()

        for start_secs, entry_idx in schedule:
            elapsed = time.monotonic() - t0
            wait = start_secs - elapsed
            if wait > 0:
                await asyncio.sleep(wait)

            self._current_entry_idx = entry_idx
            entry = self.entries[entry_idx]

            if entry.get("type") == "song":
                artist = entry.get("artist", "")
                title = entry.get("title", "")
                song = f"{artist} - {title}" if artist else title
            else:
                song = self.station_name

            encoded_mount = urllib.parse.quote(self.mount)
            url = (
                f"http://{settings.icecast_host}:{settings.icecast_port}"
                f"/admin/metadata?mount={encoded_mount}"
                f"&mode=updinfo&song={urllib.parse.quote(song)}"
            )
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(
                        url, auth=("admin", settings.icecast_source_password),
                    )
                    if resp.status_code != 200:
                        logger.warning(
                            "Icecast metadata update failed: %d", resp.status_code,
                        )
            except Exception:
                logger.debug("Failed to update icecast metadata for %s", self.mount)

    async def stop(self) -> None:
        if self._meta_task and not self._meta_task.done():
            self._meta_task.cancel()
            try:
                await self._meta_task
            except asyncio.CancelledError:
                pass
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
            logger.info("Stopped ffmpeg pid=%d", self._proc.pid)

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None


class EzstreamManager:
    def __init__(self) -> None:
        self._sessions: dict[str, EzstreamSession] = {}

    def _key(self, station_id: str, uid: str) -> str:
        return f"{station_id}:{uid}"

    async def start_session(
        self,
        station_id: str,
        uid: str,
        run_id: str,
        entries: list[dict],
        entry_index: int,
        station_name: str,
    ) -> EzstreamSession:
        key = self._key(station_id, uid)

        existing = self._sessions.get(key)
        if existing and existing.is_running:
            await existing.stop()

        mount = f"/live-{station_id[:8]}-{uid[:16]}"
        session = EzstreamSession(
            station_id=station_id,
            uid=uid,
            run_id=run_id,
            entries=entries,
            entry_index=entry_index,
            station_name=station_name,
            mount=mount,
        )
        await session.start()
        self._sessions[key] = session
        return session

    async def stop_session(self, station_id: str, uid: str) -> None:
        key = self._key(station_id, uid)
        session = self._sessions.pop(key, None)
        if session:
            await session.stop()

    def get_session(self, station_id: str, uid: str) -> EzstreamSession | None:
        key = self._key(station_id, uid)
        return self._sessions.get(key)

    async def stop_all(self) -> None:
        for session in list(self._sessions.values()):
            await session.stop()
        self._sessions.clear()
