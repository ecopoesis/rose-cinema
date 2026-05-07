from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from pathlib import Path

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
    ) -> None:
        self.station_id = station_id
        self.uid = uid
        self.run_id = run_id
        self.entries = entries
        self.entry_index = entry_index
        self.station_name = station_name
        self._proc: asyncio.subprocess.Process | None = None
        self._work_dir: Path | None = None
        self._schedule: list[tuple[float, int]] = []
        self._current_entry_idx = entry_index
        self._subscribers: list[asyncio.Queue[bytes | None]] = []
        self._pump_task: asyncio.Task[None] | None = None

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

        concat_file, self._schedule = self._build_concat_and_schedule(work_dir)

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-re",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-ar", "44100", "-ac", "2",
            "-c:a", "libmp3lame", "-b:a", settings.native_stream_bitrate,
            "-write_xing", "0", "-reservoir", "0", "-id3v2_version", "0",
            "-f", "mp3", "pipe:1",
        ]

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._pump_task = asyncio.create_task(self._pump())
        logger.info(
            "Started ffmpeg pid=%d for %s:%s (%d tracks)",
            self._proc.pid, self.station_id[:8], self.uid, len(self._schedule),
        )

    async def _pump(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            while True:
                chunk = await self._proc.stdout.read(8192)
                if not chunk:
                    break
                for q in list(self._subscribers):
                    try:
                        q.put_nowait(chunk)
                    except asyncio.QueueFull:
                        pass
        finally:
            for q in list(self._subscribers):
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    pass

    def _meta_block_for_entry(self, entry_idx: int) -> bytes:
        entry = self.entries[entry_idx] if 0 <= entry_idx < len(self.entries) else {}
        if entry.get("type") == "song":
            artist = entry.get("artist", "")
            title = entry.get("title", "")
            stream_title = f"{artist} - {title}" if artist else title
        else:
            stream_title = self.station_name or "DJ"

        meta_str = f"StreamTitle='{stream_title}';"
        raw = meta_str.encode("utf-8", errors="replace")
        padded_len = ((len(raw) + 15) // 16) * 16
        length_byte = min(padded_len // 16, 255)
        return bytes([length_byte]) + raw.ljust(padded_len, b"\x00")

    def _current_meta_block(self, elapsed: float) -> bytes:
        entry_idx = self._schedule[0][1] if self._schedule else self.entry_index
        for start_secs, idx in self._schedule:
            if elapsed >= start_secs:
                entry_idx = idx
            else:
                break
        self._current_entry_idx = entry_idx
        return self._meta_block_for_entry(entry_idx)

    def _subscribe(self) -> asyncio.Queue[bytes | None]:
        q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=512)
        self._subscribers.append(q)
        return q

    def _unsubscribe(self, q: asyncio.Queue[bytes | None]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def stream_with_icy(self, metaint: int) -> AsyncGenerator[bytes, None]:
        if not self._proc:
            return

        q = self._subscribe()
        t0 = time.monotonic()
        bytes_since_meta = 0
        meta_block = self._current_meta_block(0)

        try:
            while True:
                chunk = await q.get()
                if chunk is None:
                    break

                elapsed = time.monotonic() - t0
                meta_block = self._current_meta_block(elapsed)

                pos = 0
                while pos < len(chunk):
                    remaining = metaint - bytes_since_meta
                    take = min(remaining, len(chunk) - pos)
                    yield chunk[pos : pos + take]
                    bytes_since_meta += take
                    pos += take

                    if bytes_since_meta >= metaint:
                        yield meta_block
                        bytes_since_meta = 0
        except (GeneratorExit, asyncio.CancelledError):
            pass
        finally:
            self._unsubscribe(q)
            if not self._subscribers:
                await self.stop()

    async def stream_plain(self) -> AsyncGenerator[bytes, None]:
        if not self._proc:
            return

        q = self._subscribe()
        t0 = time.monotonic()
        try:
            while True:
                chunk = await q.get()
                if chunk is None:
                    break
                self._current_meta_block(time.monotonic() - t0)
                yield chunk
        except (GeneratorExit, asyncio.CancelledError):
            pass
        finally:
            self._unsubscribe(q)
            if not self._subscribers:
                await self.stop()

    async def stop(self) -> None:
        if self._pump_task and not self._pump_task.done():
            self._pump_task.cancel()
            try:
                await self._pump_task
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
            return existing

        session = EzstreamSession(
            station_id=station_id,
            uid=uid,
            run_id=run_id,
            entries=entries,
            entry_index=entry_index,
            station_name=station_name,
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
