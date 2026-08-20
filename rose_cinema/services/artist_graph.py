from __future__ import annotations

import logging

from rose_cinema.repositories import ArtistLinkRecord

logger = logging.getLogger(__name__)


def name_key(name: str) -> str:
    return name.lower().strip()


class ArtistGraphStore:
    """Postgres-backed cache for artist identity links and LB similar-artist results.

    Each call opens a short-lived session from the injected factory — the seed
    pool builder has no ambient session of its own. All failures degrade to
    cache misses so generation never blocks on the cache layer.
    """

    def __init__(self, session_factory):
        self._sf = session_factory

    async def get_link(self, artist_name: str) -> ArtistLinkRecord | None:
        from rose_cinema.repositories.sql import SqlArtistLinkRepository
        try:
            async with self._sf() as session:
                return await SqlArtistLinkRepository(session).get(name_key(artist_name))
        except Exception:
            logger.warning("artist_links read failed for %r", artist_name, exc_info=True)
            return None

    async def upsert_link(
        self,
        artist_name: str,
        *,
        mbid: str | None = None,
        apple_music_id: str | None = None,
    ) -> None:
        from rose_cinema.repositories.sql import SqlArtistLinkRepository
        try:
            async with self._sf() as session:
                await SqlArtistLinkRepository(session).upsert(ArtistLinkRecord(
                    name_key=name_key(artist_name),
                    name=artist_name.strip(),
                    mbid=mbid,
                    apple_music_id=apple_music_id,
                ))
        except Exception:
            logger.warning("artist_links upsert failed for %r", artist_name, exc_info=True)

    async def get_similar(self, artist_mbid: str) -> list[dict] | None:
        from rose_cinema.repositories.sql import SqlLbSimilarRepository
        try:
            async with self._sf() as session:
                return await SqlLbSimilarRepository(session).get(artist_mbid)
        except Exception:
            logger.warning("lb_similar_cache read failed for %s", artist_mbid, exc_info=True)
            return None

    async def put_similar(self, artist_mbid: str, payload: list[dict]) -> None:
        from rose_cinema.repositories.sql import SqlLbSimilarRepository
        try:
            async with self._sf() as session:
                await SqlLbSimilarRepository(session).put(artist_mbid, payload)
        except Exception:
            logger.warning("lb_similar_cache write failed for %s", artist_mbid, exc_info=True)
