from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MBRecording:
    mbid: str
    title: str
    year: int | None
    rg_type: str
    release_group: str


_ARTIST_MBID_SQL = """
SELECT gid::text AS mbid FROM musicbrainz.artist WHERE lower(name) = lower(:name)
UNION
SELECT a.gid::text AS mbid
FROM musicbrainz.artist a
JOIN musicbrainz.artist_alias al ON al.artist = a.id
WHERE lower(al.name) = lower(:name)
LIMIT 1
"""

_DISCOGRAPHY_SQL = """
SELECT DISTINCT ON (r.gid)
       r.gid::text AS recording_mbid,
       r.name AS title,
       rg.name AS release_group,
       pt.name AS rg_type,
       rgm.first_release_date_year AS year
FROM musicbrainz.artist a
JOIN musicbrainz.artist_credit_name acn ON acn.artist = a.id
JOIN musicbrainz.recording r ON r.artist_credit = acn.artist_credit AND NOT r.video
JOIN musicbrainz.track t ON t.recording = r.id
JOIN musicbrainz.medium m ON t.medium = m.id
JOIN musicbrainz.release rel ON m.release = rel.id
JOIN musicbrainz.release_group rg ON rel.release_group = rg.id
JOIN musicbrainz.release_group_meta rgm ON rgm.id = rg.id
JOIN musicbrainz.release_group_primary_type pt ON rg.type = pt.id
WHERE a.gid = CAST(:artist_mbid AS uuid)
  AND pt.name IN ('Album', 'EP', 'Single')
  AND NOT EXISTS (
      SELECT 1 FROM musicbrainz.release_group_secondary_type_join stj
      JOIN musicbrainz.release_group_secondary_type st ON st.id = stj.secondary_type
      WHERE stj.release_group = rg.id
        AND st.name IN ('Live', 'Compilation', 'Remix', 'DJ-mix', 'Soundtrack', 'Interview')
  )
LIMIT 2000
"""

_TYPE_PRIORITY = {"Album": 0, "EP": 1, "Single": 2}


class MusicBrainzMirror:
    """Direct SQL against a local MusicBrainz DB-only mirror.

    Every query is wrapped so mirror trouble (down, annual schema change)
    degrades to "no data" with a loud log rather than failing generation.
    """

    def __init__(self, db_url: str):
        self._engine = create_async_engine(db_url, pool_size=2, max_overflow=2)

    async def get_artist_mbid(self, name: str) -> str | None:
        try:
            async with self._engine.connect() as conn:
                row = (await conn.execute(text(_ARTIST_MBID_SQL), {"name": name})).first()
            return row.mbid if row else None
        except Exception:
            logger.warning("MB mirror artist lookup failed for %r", name, exc_info=True)
            return None

    async def get_discography(self, artist_mbid: str, limit: int = 200) -> list[MBRecording]:
        try:
            async with self._engine.connect() as conn:
                rows = (
                    await conn.execute(text(_DISCOGRAPHY_SQL), {"artist_mbid": artist_mbid})
                ).all()
        except Exception:
            logger.warning(
                "MB mirror discography query failed for %s", artist_mbid, exc_info=True,
            )
            return []

        # One recording per title: prefer Album releases, then the earliest year.
        by_title: dict[str, MBRecording] = {}
        for row in rows:
            rec = MBRecording(
                mbid=row.recording_mbid,
                title=row.title,
                year=row.year,
                rg_type=row.rg_type,
                release_group=row.release_group,
            )
            key = rec.title.lower().strip()
            if not key:
                continue
            current = by_title.get(key)
            if current is None or _pref_key(rec) < _pref_key(current):
                by_title[key] = rec
        out = sorted(by_title.values(), key=_pref_key)
        return out[:limit]


def _pref_key(rec: MBRecording) -> tuple[int, int]:
    return (_TYPE_PRIORITY.get(rec.rg_type, 9), rec.year or 9999)


_singleton: MusicBrainzMirror | None = None


def get_mb_mirror() -> MusicBrainzMirror | None:
    global _singleton
    from rose_cinema.config import settings

    if not settings.musicbrainz_db_url:
        return None
    if _singleton is None:
        _singleton = MusicBrainzMirror(settings.musicbrainz_db_url)
    return _singleton
