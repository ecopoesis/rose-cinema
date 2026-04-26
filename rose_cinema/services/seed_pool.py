from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from rose_cinema.providers import LLMMessage, LLMProvider
from rose_cinema.services.catalog import (
    CatalogArtist,
    CatalogTrack,
    MusicCatalog,
)

logger = logging.getLogger(__name__)


SeedSource = Literal["artist_seed", "track_seed", "theme_seed", "none"]


@dataclass
class SeedPool:
    seed_label: str
    seed_artist: CatalogArtist | None
    tracks: list[CatalogTrack]
    artists: list[CatalogArtist]
    source: SeedSource


# In-memory TTL cache live on the SeedPoolBuilder instance.
_CACHE_TTL_SECS = 24 * 3600
_DEFAULT_PER_ARTIST_POOL_CAP = 4
_SEED_ARTIST_POOL_CAP = 5
_SIMILAR_ARTIST_FANOUT = 12
_SIMILAR_ARTIST_TOP_SONGS = 8
_SEED_ARTIST_TOP_SONGS = 10
_FETCH_CONCURRENCY = 6


class SeedPoolBuilder:
    """Build a candidate-track pool from a free-text seed.

    Three discovery paths:
      A) artist_seed   — seed resolves directly to an artist; expand via similar-artists graph.
      B) track_seed    — seed resolves to a song; use the song's artist for path A.
      C) theme_seed    — neither resolves; LLM maps theme to genre names; pull genre charts.

    Falls through to source="none" with empty tracks if nothing produces a pool, in
    which case TrackPicker reverts to the legacy LLM-discovers-then-verifies path.
    """

    def __init__(self, catalog: MusicCatalog, llm: LLMProvider):
        self._catalog = catalog
        self._llm = llm
        self._cache: dict[tuple[str, int], tuple[float, SeedPool]] = {}

    async def build(self, music_source: str, target_count: int) -> SeedPool:
        seed_label = music_source.strip()
        cache_key = (seed_label.lower(), (target_count // 5) * 5)
        cached = self._cache.get(cache_key)
        if cached and cached[0] > time.time():
            logger.info("seed_pool cache hit: %r (source=%s, %d tracks)",
                        seed_label, cached[1].source, len(cached[1].tracks))
            return cached[1]

        pool = await self._build_uncached(seed_label, target_count)
        self._cache[cache_key] = (time.time() + _CACHE_TTL_SECS, pool)
        logger.info(
            "seed_pool built: seed=%r source=%s tracks=%d unique_artists=%d",
            seed_label, pool.source, len(pool.tracks), len(pool.artists),
        )
        return pool

    async def _build_uncached(self, seed_label: str, target_count: int) -> SeedPool:
        # Path A: try as artist
        seed_artist = await self._resolve_artist(seed_label)

        # Path B: try as track → artist
        if seed_artist is None:
            seed_artist = await self._resolve_via_track(seed_label)

        if seed_artist is not None:
            return await self._build_artist_pool(seed_label, seed_artist, target_count)

        # Path C: theme → genre charts
        themed = await self._build_theme_pool(seed_label, target_count)
        if themed.tracks:
            return themed

        return SeedPool(
            seed_label=seed_label, seed_artist=None,
            tracks=[], artists=[], source="none",
        )

    # ── Path A helpers ─────────────────────────────────────────────────

    async def _resolve_artist(self, seed_label: str) -> CatalogArtist | None:
        results = await self._catalog.search_artists(seed_label, limit=3)
        if not results:
            return None
        seed_lower = seed_label.lower()
        for a in results:
            n = a.name.lower()
            if n == seed_lower or n in seed_lower or seed_lower in n:
                return a
        return results[0]   # weak match — still usable

    async def _resolve_via_track(self, seed_label: str) -> CatalogArtist | None:
        tracks = await self._catalog.search(seed_label, limit=3)
        if not tracks:
            return None
        first = tracks[0]
        artists = await self._catalog.search_artists(first.artist, limit=1)
        if artists:
            return artists[0]
        return None

    async def _build_artist_pool(
        self, seed_label: str, seed_artist: CatalogArtist, target_count: int,
    ) -> SeedPool:
        seed_views = await self._catalog.get_artist_views(
            seed_artist.apple_music_id,
            top_songs=_SEED_ARTIST_TOP_SONGS,
            similar_artists=_SIMILAR_ARTIST_FANOUT,
        )

        sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

        async def fetch_one(a: CatalogArtist):
            async with sem:
                return await self._catalog.get_artist_views(
                    a.apple_music_id,
                    top_songs=_SIMILAR_ARTIST_TOP_SONGS,
                    similar_artists=0,
                )

        related_views = await asyncio.gather(
            *(fetch_one(a) for a in seed_views.similar_artists),
            return_exceptions=True,
        )

        # Collect tracks per artist, with seed-specific cap
        by_artist: dict[str, list[CatalogTrack]] = defaultdict(list)
        artists_seen: dict[str, CatalogArtist] = {}

        artists_seen[seed_artist.apple_music_id] = seed_views.artist
        for t in seed_views.top_songs[:_SEED_ARTIST_POOL_CAP]:
            by_artist[seed_artist.apple_music_id].append(t)

        for v in related_views:
            if isinstance(v, BaseException):
                continue
            if not v.artist.apple_music_id:
                continue
            artists_seen.setdefault(v.artist.apple_music_id, v.artist)
            for t in v.top_songs[:_DEFAULT_PER_ARTIST_POOL_CAP]:
                by_artist[v.artist.apple_music_id].append(t)

        # Optional: under-target safety hop
        pool_target = max(40, target_count * 6)
        if sum(len(ts) for ts in by_artist.values()) < pool_target // 2:
            await self._second_hop(seed_views.similar_artists[:3], by_artist, artists_seen, sem)

        return self._assemble_pool(
            seed_label=seed_label,
            seed_artist=seed_views.artist,
            by_artist=by_artist,
            artists_seen=artists_seen,
            source="artist_seed",
        )

    async def _second_hop(
        self,
        seeds: list[CatalogArtist],
        by_artist: dict[str, list[CatalogTrack]],
        artists_seen: dict[str, CatalogArtist],
        sem: asyncio.Semaphore,
    ) -> None:
        """Pull one more layer out when the first hop returned thin."""
        async def fetch(a: CatalogArtist):
            async with sem:
                return await self._catalog.get_artist_views(
                    a.apple_music_id,
                    top_songs=0,
                    similar_artists=8,
                )

        layer = await asyncio.gather(*(fetch(a) for a in seeds), return_exceptions=True)
        next_artists: list[CatalogArtist] = []
        for v in layer:
            if isinstance(v, BaseException):
                continue
            for a in v.similar_artists:
                if a.apple_music_id and a.apple_music_id not in artists_seen:
                    next_artists.append(a)

        async def fetch_top(a: CatalogArtist):
            async with sem:
                return await self._catalog.get_artist_views(
                    a.apple_music_id, top_songs=6, similar_artists=0,
                )

        results = await asyncio.gather(
            *(fetch_top(a) for a in next_artists[:8]),
            return_exceptions=True,
        )
        for v in results:
            if isinstance(v, BaseException):
                continue
            if not v.artist.apple_music_id:
                continue
            artists_seen.setdefault(v.artist.apple_music_id, v.artist)
            for t in v.top_songs[:_DEFAULT_PER_ARTIST_POOL_CAP]:
                by_artist[v.artist.apple_music_id].append(t)

    # ── Path C helpers ─────────────────────────────────────────────────

    async def _build_theme_pool(self, seed_label: str, target_count: int) -> SeedPool:
        genres = await self._catalog.list_genres()
        if not genres:
            return SeedPool(
                seed_label=seed_label, seed_artist=None,
                tracks=[], artists=[], source="none",
            )

        names = await self._llm_pick_genres(seed_label, list(genres.keys()))
        chosen_ids = [genres[n] for n in names if n in genres]
        if not chosen_ids:
            return SeedPool(
                seed_label=seed_label, seed_artist=None,
                tracks=[], artists=[], source="none",
            )

        sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

        async def fetch(gid: str):
            async with sem:
                return await self._catalog.get_genre_top_songs(gid, limit=30)

        all_lists = await asyncio.gather(*(fetch(gid) for gid in chosen_ids), return_exceptions=True)

        by_artist: dict[str, list[CatalogTrack]] = defaultdict(list)
        artists_seen: dict[str, CatalogArtist] = {}
        for tlist in all_lists:
            if isinstance(tlist, BaseException):
                continue
            for t in tlist:
                # synthesize a stand-in CatalogArtist (we don't have a real artist_id from charts)
                artist_key = t.artist.lower().strip()
                if not artist_key:
                    continue
                if artist_key not in artists_seen:
                    artists_seen[artist_key] = CatalogArtist(
                        apple_music_id="", name=t.artist,
                    )
                by_artist[artist_key].append(t)

        return self._assemble_pool(
            seed_label=seed_label,
            seed_artist=None,
            by_artist=by_artist,
            artists_seen=artists_seen,
            source="theme_seed",
        )

    async def _llm_pick_genres(self, theme: str, available: list[str]) -> list[str]:
        system = (
            "You map a theme/mood/description to 1-3 Apple Music genre names from a fixed list.\n"
            f"Available genres: {', '.join(available)}\n"
            'Output ONLY a JSON object: {"genres": ["Genre Name", ...]}.\n'
            "Pick names verbatim from the list. No commentary."
        )
        user = f"Theme: {theme}"
        try:
            raw = await self._llm.complete(
                messages=[LLMMessage("system", system), LLMMessage("user", user)],
                temperature=0.4, max_tokens=120,
            )
        except Exception:
            logger.exception("LLM theme→genre call failed")
            return []
        try:
            obj_text = _extract_json_object(raw)
            obj = json.loads(obj_text)
        except Exception:
            logger.warning("LLM theme→genre returned unparsable: %r", raw[:200])
            return []
        names = obj.get("genres") or []
        return [n for n in names if isinstance(n, str)]

    # ── shared assembly ────────────────────────────────────────────────

    def _assemble_pool(
        self,
        *,
        seed_label: str,
        seed_artist: CatalogArtist | None,
        by_artist: dict[str, list[CatalogTrack]],
        artists_seen: dict[str, CatalogArtist],
        source: SeedSource,
    ) -> SeedPool:
        # De-dup by apple_music_id across the whole pool.
        seen_ids: set[str] = set()
        cleaned: dict[str, list[CatalogTrack]] = {}
        for aid, tracks in by_artist.items():
            keep: list[CatalogTrack] = []
            for t in tracks:
                if t.apple_music_id and t.apple_music_id in seen_ids:
                    continue
                if t.apple_music_id:
                    seen_ids.add(t.apple_music_id)
                keep.append(t)
            if keep:
                cleaned[aid] = keep

        # Round-robin interleave so the LLM doesn't see one artist's whole catalog up front.
        ordered: list[CatalogTrack] = []
        keys = list(cleaned.keys())
        idx = 0
        while any(cleaned[k] for k in keys):
            k = keys[idx % len(keys)]
            if cleaned[k]:
                ordered.append(cleaned[k].pop(0))
            idx += 1

        artists = [artists_seen[k] for k in artists_seen]

        return SeedPool(
            seed_label=seed_label,
            seed_artist=seed_artist,
            tracks=ordered,
            artists=artists,
            source=source,
        )


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_object(text: str) -> str:
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        raise ValueError(f"no JSON object found in: {text[:200]!r}")
    return m.group(0)
