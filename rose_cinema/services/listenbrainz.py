from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LBSimilarArtist:
    mbid: str
    name: str
    score: int


class ListenBrainzClient:
    """Thin client for the ListenBrainz Labs similar-artists endpoint.

    Stateless HTTP only — Postgres caching lives in ArtistGraphStore so this
    stays testable and session-free, like MusicBrainzClient.
    """

    def __init__(self, base_url: str, algorithm: str):
        self._algorithm = algorithm
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=15.0)

    async def get_similar_artists(self, artist_mbid: str) -> list[LBSimilarArtist]:
        try:
            resp = await self._client.get(
                "/similar-artists/json",
                params={"artist_mbids": artist_mbid, "algorithm": self._algorithm},
            )
            if resp.status_code != 200:
                logger.warning(
                    "ListenBrainz similar-artists %s for mbid %s",
                    resp.status_code, artist_mbid,
                )
                return []
            data = resp.json()
        except Exception:
            logger.warning(
                "ListenBrainz similar-artists failed for mbid %s", artist_mbid,
                exc_info=True,
            )
            return []

        results: list[LBSimilarArtist] = []
        for item in data if isinstance(data, list) else []:
            mbid = str(item.get("artist_mbid") or item.get("mbid") or "").strip()
            name = str(item.get("name") or "").strip()
            if not mbid or not name:
                continue
            try:
                score = int(item.get("score") or 0)
            except (TypeError, ValueError):
                score = 0
            results.append(LBSimilarArtist(mbid=mbid, name=name, score=score))
        results.sort(key=lambda a: a.score, reverse=True)
        return results


_singleton: ListenBrainzClient | None = None


def get_listenbrainz_client() -> ListenBrainzClient | None:
    global _singleton
    from rose_cinema.config import settings

    if not settings.listenbrainz_enabled:
        return None
    if _singleton is None:
        _singleton = ListenBrainzClient(
            settings.listenbrainz_base_url, settings.listenbrainz_algorithm,
        )
    return _singleton
