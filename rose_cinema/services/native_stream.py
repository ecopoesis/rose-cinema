from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rose_cinema.config import settings
from rose_cinema.repositories import ListenPositionRecord
from rose_cinema.repositories.sql import SqlListenPositionRepository
from rose_cinema.services.apple_music_stream import AppleMusicStreamer

logger = logging.getLogger(__name__)


@dataclass
class NativeStreamSession:
    station_id: str
    uid: str
    run_id: str
    entries: list[dict]
    entry_index: int
    station_name: str
    art_url: str | None
    session_factory: async_sessionmaker[AsyncSession]
    streamer: AppleMusicStreamer

    _current_entry: dict = field(default_factory=dict)
    _cancelled: bool = False

    @property
    def current_entry(self) -> dict:
        return self._current_entry

    async def stream_with_icy(self, metaint: int) -> AsyncGenerator[bytes, None]:
        bytes_since_meta = 0
        meta_block = b"\x00"

        try:
            for idx in range(self.entry_index, len(self.entries)):
                if self._cancelled:
                    break

                entry = self.entries[idx]
                self._current_entry = entry
                self.entry_index = idx
                meta_block = _build_icy_block(entry, self.station_name, self.art_url)

                source = (
                    self._stream_dj(entry)
                    if entry.get("type") == "dj"
                    else self._stream_song(entry)
                )

                async for audio_chunk in source:
                    pos = 0
                    while pos < len(audio_chunk):
                        remaining = metaint - bytes_since_meta
                        take = min(remaining, len(audio_chunk) - pos)
                        yield audio_chunk[pos : pos + take]
                        bytes_since_meta += take
                        pos += take

                        if bytes_since_meta >= metaint:
                            yield meta_block
                            bytes_since_meta = 0

                await self._save_position()
                logger.info(
                    "Finished entry %d/%d (%s) for %s:%s",
                    idx, len(self.entries), entry.get("type"), self.station_id, self.uid,
                )
        except (GeneratorExit, asyncio.CancelledError):
            pass
        finally:
            self._cancelled = True
            await self._save_position()

    async def stream_plain(self) -> AsyncGenerator[bytes, None]:
        try:
            for idx in range(self.entry_index, len(self.entries)):
                if self._cancelled:
                    break

                entry = self.entries[idx]
                self._current_entry = entry
                self.entry_index = idx

                source = (
                    self._stream_dj(entry)
                    if entry.get("type") == "dj"
                    else self._stream_song(entry)
                )
                async for chunk in source:
                    yield chunk

                await self._save_position()
        except (GeneratorExit, asyncio.CancelledError):
            pass
        finally:
            self._cancelled = True
            await self._save_position()

    async def _stream_dj(self, entry: dict) -> AsyncGenerator[bytes, None]:
        audio_file = entry.get("audio_file", "")
        if not audio_file:
            logger.warning("DJ entry has no audio_file, skipping")
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "warning",
                "-re", "-i", audio_file,
                "-ar", "44100", "-ac", "2",
                "-c:a", "libmp3lame", "-b:a", settings.native_stream_bitrate,
                "-write_xing", "0", "-reservoir", "0", "-id3v2_version", "0", "-f", "mp3", "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                while True:
                    chunk = await proc.stdout.read(8192)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if proc.returncode is None:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        proc.kill()
                stderr = await proc.stderr.read()
                if stderr and proc.returncode != 0:
                    logger.warning("ffmpeg DJ stderr for %s: %s", audio_file, stderr.decode(errors="replace")[:500])
        except FileNotFoundError:
            logger.warning("DJ audio file not found: %s", audio_file)

    async def _stream_song(self, entry: dict) -> AsyncGenerator[bytes, None]:
        apple_id = entry.get("apple_music_id")
        if not apple_id:
            logger.warning("Song entry has no apple_music_id, skipping")
            return
        try:
            logger.info(
                "Streaming %s - %s (apple_music_id=%s)",
                entry.get("artist", "?"), entry.get("title", "?"), apple_id,
            )
            async for chunk in self.streamer.stream_track_as_mp3(apple_id):
                yield chunk
        except Exception:
            logger.exception("Failed to stream track %s", apple_id)

    async def _save_position(self) -> None:
        try:
            async with self.session_factory() as session:
                repo = SqlListenPositionRepository(session)
                await repo.upsert_position(ListenPositionRecord(
                    station_id=self.station_id,
                    uid=self.uid,
                    run_id=self.run_id,
                    entry_index=self.entry_index,
                    listened_date=date.today().isoformat(),
                ))
        except Exception:
            logger.exception("Failed to save position for %s:%s", self.station_id, self.uid)

    def cancel(self) -> None:
        self._cancelled = True


def _build_icy_block(entry: dict, station_name: str, station_art: str | None) -> bytes:
    if entry.get("type") == "song":
        artist = entry.get("artist", "")
        title = entry.get("title", "")
        stream_title = f"{artist} - {title}" if artist else title
        artwork = entry.get("artwork_url", "")
    else:
        stream_title = station_name or "DJ"
        artwork = station_art or ""

    meta_str = f"StreamTitle='{stream_title}';"
    if artwork:
        meta_str += f"StreamUrl='{artwork}';"

    raw = meta_str.encode("utf-8", errors="replace")
    padded_len = ((len(raw) + 15) // 16) * 16
    length_byte = min(padded_len // 16, 255)
    if length_byte == 255:
        raw = raw[:4080]
        padded_len = 4080
    return bytes([length_byte]) + raw.ljust(padded_len, b"\x00")


