from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time

import httpx

logger = logging.getLogger(__name__)

_MB_API = "https://musicbrainz.org/ws/2"
_MIN_REQUEST_INTERVAL = 1.1
_MATCH_THRESHOLD = 0.85
_MAX_TAGS = 8
_MIN_TAG_COUNT = 1

_ARTICLE_RE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)


def _normalize_name(name: str) -> str:
    n = name.lower().strip()
    n = _ARTICLE_RE.sub("", n)
    return n


class MusicBrainzClient:

    def __init__(self, user_agent: str):
        self._client = httpx.AsyncClient(
            base_url=_MB_API,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=15.0,
        )
        self._last_request: float = 0.0
        self._lock = asyncio.Lock()
        self._name_to_mbid: dict[str, str | None] = {}
        self._mbid_to_tags: dict[str, tuple[str, ...]] = {}

    async def get_artist_tags(self, artist_name: str) -> tuple[str, ...]:
        try:
            mbid = await self._search_artist(artist_name)
            if mbid is None:
                return ()
            return await self._lookup_tags(mbid)
        except Exception:
            logger.warning("MusicBrainz lookup failed for %r", artist_name, exc_info=True)
            return ()

    async def get_artist_mbid(self, artist_name: str) -> str | None:
        try:
            return await self._search_artist(artist_name)
        except Exception:
            logger.warning("MusicBrainz MBID lookup failed for %r", artist_name, exc_info=True)
            return None

    async def get_artist_tags_by_mbid(self, mbid: str) -> tuple[str, ...]:
        try:
            return await self._lookup_tags(mbid)
        except Exception:
            logger.warning("MusicBrainz tag lookup failed for mbid %r", mbid, exc_info=True)
            return ()

    async def _search_artist(self, name: str) -> str | None:
        key = _normalize_name(name)
        if key in self._name_to_mbid:
            return self._name_to_mbid[key]

        data = await self._rate_limited_get(
            "/artist/", {"query": f'artist:"{name}"', "fmt": "json", "limit": "5"},
        )
        artists = data.get("artists", [])
        if not artists:
            self._name_to_mbid[key] = None
            return None

        best_score = 0.0
        best_id: str | None = None
        for a in artists:
            mb_name = a.get("name", "")
            score = difflib.SequenceMatcher(
                None, _normalize_name(mb_name), key,
            ).ratio()
            if score > best_score:
                best_score = score
                best_id = a.get("id")

        if best_score < _MATCH_THRESHOLD or best_id is None:
            logger.debug("No good MB match for %r (best %.2f)", name, best_score)
            self._name_to_mbid[key] = None
            return None

        self._name_to_mbid[key] = best_id
        return best_id

    async def _lookup_tags(self, mbid: str) -> tuple[str, ...]:
        if mbid in self._mbid_to_tags:
            return self._mbid_to_tags[mbid]

        data = await self._rate_limited_get(
            f"/artist/{mbid}", {"inc": "tags+genres", "fmt": "json"},
        )

        raw_tags: list[tuple[int, str]] = []
        for tag in data.get("tags", []):
            count = tag.get("count", 0)
            tag_name = tag.get("name", "").strip()
            if count >= _MIN_TAG_COUNT and tag_name:
                raw_tags.append((count, tag_name))
        for genre in data.get("genres", []):
            count = genre.get("count", 0)
            genre_name = genre.get("name", "").strip()
            if genre_name:
                raw_tags.append((max(count, _MIN_TAG_COUNT), genre_name))

        seen: set[str] = set()
        deduped: list[tuple[int, str]] = []
        for count, name in raw_tags:
            low = name.lower()
            if low not in seen:
                seen.add(low)
                deduped.append((count, name))

        deduped.sort(key=lambda x: x[0], reverse=True)
        tags = tuple(name for _, name in deduped[:_MAX_TAGS])

        self._mbid_to_tags[mbid] = tags
        return tags

    async def _rate_limited_get(self, path: str, params: dict) -> dict:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request
            if elapsed < _MIN_REQUEST_INTERVAL:
                await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)
            self._last_request = time.monotonic()
            resp = await self._client.get(path, params=params)
            if resp.status_code == 503:
                logger.warning("MusicBrainz 503 (rate limited)")
                return {}
            resp.raise_for_status()
            return resp.json()


_singleton: MusicBrainzClient | None = None


def get_musicbrainz_client() -> MusicBrainzClient | None:
    global _singleton
    from rose_cinema.config import settings

    if not settings.musicbrainz_user_agent:
        return None
    if _singleton is None:
        _singleton = MusicBrainzClient(settings.musicbrainz_user_agent)
    return _singleton
