from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path

import httpx
from mutagen.mp4 import MP4, MP4Cover
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rose_cinema.config import settings
from rose_cinema.repositories import CachedTrackRecord
from rose_cinema.repositories.sql import SqlCachedTrackRepository
from rose_cinema.services.apple_music_stream import AppleMusicStreamer
from rose_cinema.services.catalog import MusicCatalog

logger = logging.getLogger(__name__)

_UNSAFE_RE = re.compile(r'[\\/:*?"<>|]')


def _sanitize(name: str) -> str:
    return _UNSAFE_RE.sub("_", name).strip(". ") or "_"


def _picard_path(
    artist: str, album: str, year: str, track_number: int, title: str,
) -> str:
    safe_artist = _sanitize(artist) if artist else "Unknown Artist"
    safe_album = _sanitize(album) if album else "Unknown Album"
    safe_title = _sanitize(title) if title else "Unknown Title"
    safe_year = year[:4] if year else "0000"
    num = f"{track_number:02d}" if track_number else "00"
    return f"{safe_artist} - ({safe_year}) {safe_album}/{num} - {safe_title}.m4a"


async def _fetch_artwork(artwork_obj: dict) -> bytes | None:
    url_template = artwork_obj.get("url", "")
    if not url_template:
        return None
    url = url_template.replace("{w}", "600").replace("{h}", "600")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception:
        logger.warning("Failed to fetch artwork from %s", url[:80])
        return None


async def _tag_m4a(
    path: Path,
    apple_music_id: str,
    catalog: MusicCatalog | None,
    basic_meta: dict,
) -> None:
    attrs: dict = {}
    if catalog:
        try:
            attrs = await catalog.get_song(apple_music_id)
        except Exception:
            logger.warning("Failed to fetch full metadata for %s", apple_music_id)

    audio = MP4(str(path))
    if audio.tags is None:
        audio.add_tags()

    title = attrs.get("name") or basic_meta.get("title", "")
    artist = attrs.get("artistName") or basic_meta.get("artist", "")
    album = attrs.get("albumName") or basic_meta.get("album", "")
    year = (attrs.get("releaseDate") or basic_meta.get("year", ""))[:4]
    track_num = attrs.get("trackNumber") or basic_meta.get("track_number", 0)

    if title:
        audio.tags["\xa9nam"] = [title]
    if artist:
        audio.tags["\xa9ART"] = [artist]
    if album:
        audio.tags["\xa9alb"] = [album]
    if year:
        audio.tags["\xa9day"] = [year]
    if track_num:
        track_total = attrs.get("trackCount", 0) or 0
        audio.tags["trkn"] = [(track_num, track_total)]

    disc_num = attrs.get("discNumber", 0) or 0
    if disc_num:
        audio.tags["disk"] = [(disc_num, 0)]

    composer = attrs.get("composerName") or ""
    if composer:
        audio.tags["\xa9wrt"] = [composer]

    genres = attrs.get("genreNames") or []
    if genres:
        audio.tags["\xa9gen"] = [genres[0]]

    copyright_text = attrs.get("copyright") or ""
    if copyright_text:
        audio.tags["cprt"] = [copyright_text]

    album_artist = attrs.get("artistName") or ""
    if album_artist:
        audio.tags["aART"] = [album_artist]

    content_rating = attrs.get("contentRating") or ""
    if content_rating == "explicit":
        audio.tags["rtng"] = [1]

    artwork_obj = attrs.get("artwork") or {}
    if artwork_obj:
        art_data = await _fetch_artwork(artwork_obj)
        if art_data:
            fmt = MP4Cover.FORMAT_JPEG
            if art_data[:8] == b"\x89PNG\r\n\x1a\n":
                fmt = MP4Cover.FORMAT_PNG
            audio.tags["covr"] = [MP4Cover(art_data, imageformat=fmt)]

    audio.save()
    logger.info("Tagged %s with Apple Music metadata", path.name)


class TrackCache:
    def __init__(
        self,
        streamer: AppleMusicStreamer,
        session_factory: async_sessionmaker[AsyncSession],
        catalog: MusicCatalog | None = None,
    ) -> None:
        self._streamer = streamer
        self._dir = Path(settings.tracks_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._delay = settings.track_download_delay
        self._sf = session_factory
        self._catalog = catalog

    async def path_for(self, apple_music_id: str) -> Path | None:
        async with self._sf() as session:
            repo = SqlCachedTrackRepository(session)
            record = await repo.get(apple_music_id)
        if not record:
            return None
        p = self._dir / record.path
        if p.exists() and p.stat().st_size > 0:
            return p
        return None

    async def is_cached(self, apple_music_id: str) -> bool:
        return await self.path_for(apple_music_id) is not None

    async def resolve_paths(self, apple_music_ids: list[str]) -> dict[str, Path]:
        if not apple_music_ids:
            return {}
        async with self._sf() as session:
            repo = SqlCachedTrackRepository(session)
            records = await repo.get_many(apple_music_ids)
        result: dict[str, Path] = {}
        for aid, rec in records.items():
            p = self._dir / rec.path
            if p.exists() and p.stat().st_size > 0:
                result[aid] = p
        return result

    async def download_track(
        self,
        apple_music_id: str,
        *,
        artist: str = "",
        album: str = "",
        year: str = "",
        track_number: int = 0,
        title: str = "",
    ) -> Path:
        existing = await self.path_for(apple_music_id)
        if existing:
            return existing

        rel_path = _picard_path(artist, album, year, track_number, title)
        dest = self._dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)

        info = await self._streamer.get_stream_info(apple_music_id)

        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y"]
        if info.is_encrypted and info.decryption_key:
            cmd += ["-decryption_key", info.decryption_key]
        cmd += ["-i", info.stream_url]
        cmd += ["-c:a", "copy"]

        fd, tmp_path = tempfile.mkstemp(suffix=".m4a", dir=str(dest.parent))
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
            logger.info("Cached track %s -> %s", apple_music_id, rel_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        try:
            await _tag_m4a(dest, apple_music_id, self._catalog, {
                "title": title, "artist": artist, "album": album,
                "year": year, "track_number": track_number,
            })
        except Exception:
            logger.warning("Failed to tag %s, file saved without metadata", dest.name)

        async with self._sf() as session:
            repo = SqlCachedTrackRepository(session)
            await repo.upsert(CachedTrackRecord(
                apple_music_id=apple_music_id,
                path=rel_path,
                artist=artist,
                album=album,
                title=title,
                year=year,
                track_number=track_number,
            ))

        return dest

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

        aids = [s["apple_music_id"] for s in songs]
        cached = await self.resolve_paths(aids)
        downloaded = 0

        for i, song in enumerate(songs):
            aid = song["apple_music_id"]
            if aid in cached:
                continue
            if max_downloads and downloaded >= max_downloads:
                break
            try:
                path = await self.download_track(
                    aid,
                    artist=song.get("artist", ""),
                    album=song.get("album", ""),
                    year=song.get("year", ""),
                    track_number=song.get("track_number", 0),
                    title=song.get("title", ""),
                )
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
