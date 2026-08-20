from __future__ import annotations

import asyncio
import json
import logging
import random
import re
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


SeedSource = Literal["artist_seed", "multi_artist_seed", "track_seed", "theme_seed", "none"]


@dataclass
class ParsedSource:
    artists: list[str] = field(default_factory=list)
    albums: list[str] = field(default_factory=list)
    tracks: list[str] = field(default_factory=list)
    style: str = ""
    constraints: str = ""


@dataclass(frozen=True)
class VarietyParams:
    genre: float = 0.5
    year: float = 0.5
    popularity: float = 0.0


@dataclass
class SeedPool:
    seed_label: str
    seed_artist: CatalogArtist | None
    tracks: list[CatalogTrack]
    artists: list[CatalogArtist]
    source: SeedSource
    artist_tags: dict[str, tuple[str, ...]] = field(default_factory=dict)
    style_hint: str = ""
    constraints: str = ""
    variety: VarietyParams = field(default_factory=VarietyParams)


_DEFAULT_PER_ARTIST_POOL_CAP = 4
_SEED_ARTIST_POOL_CAP = 5
_SIMILAR_ARTIST_FANOUT = 12
_SIMILAR_ARTIST_PICK = 8
_SIMILAR_ARTIST_TOP_SONGS = 12
_SEED_ARTIST_TOP_SONGS = 10
_FETCH_CONCURRENCY = 6
_LB_SIMILAR_TAKE = 20
_LB_RESOLVE_BUDGET = 6


class SeedPoolBuilder:
    """Build a candidate-track pool from a free-text seed.

    Three discovery paths:
      A) artist_seed   — seed resolves directly to an artist; expand via similar-artists graph.
      B) track_seed    — seed resolves to a song; use the song's artist for path A.
      C) theme_seed    — neither resolves; LLM maps theme to genre names; pull genre charts.

    Falls through to source="none" with empty tracks if nothing produces a pool, in
    which case TrackPicker reverts to the legacy LLM-discovers-then-verifies path.
    """

    def __init__(
        self,
        catalog: MusicCatalog,
        llm: LLMProvider,
        mb_client: object | None = None,
        lb_client: object | None = None,
        graph_store: object | None = None,
    ):
        self._catalog = catalog
        self._llm = llm
        self._mb = mb_client
        self._lb = lb_client
        self._graph = graph_store

    async def build(
        self,
        music_source: str,
        target_count: int,
        *,
        source_artists: list[str] | None = None,
        source_albums: list[str] | None = None,
        source_tracks: list[str] | None = None,
        discovery_rate: float = 0.5,
        genre_variety: float = 0.5,
        year_variety: float = 0.5,
        popularity_variety: float = 0.0,
    ) -> SeedPool:
        seed_label = music_source.strip()

        all_artists = list(source_artists or [])
        all_albums = list(source_albums or [])
        all_tracks = list(source_tracks or [])

        parsed = await self._maybe_pre_parse(seed_label, all_artists)
        if parsed:
            all_artists.extend(parsed.artists)
            all_albums.extend(parsed.albums)
            all_tracks.extend(parsed.tracks)

        all_artists = _dedup_names(all_artists)
        all_albums = _dedup_names(all_albums)
        all_tracks = _dedup_names(all_tracks)

        style_hint = parsed.style if parsed else ""
        constraints = parsed.constraints if parsed else ""

        if len(all_artists) > 1:
            pool = await self._build_multi_artist_pool(
                seed_label, all_artists, target_count, discovery_rate, genre_variety,
            )
        elif len(all_artists) == 1:
            seed_artist = await self._resolve_artist(all_artists[0])
            if seed_artist:
                pool = await self._build_artist_pool(
                    seed_label, seed_artist, target_count, discovery_rate, genre_variety,
                )
            else:
                pool = await self._build_uncached(
                    seed_label, target_count, discovery_rate, genre_variety,
                )
        else:
            pool = await self._build_uncached(
                seed_label, target_count, discovery_rate, genre_variety,
            )

        if all_tracks:
            await self._add_explicit_tracks(pool, all_tracks)
        if all_albums:
            await self._add_album_tracks(pool, all_albums)

        pool.style_hint = style_hint
        pool.constraints = constraints
        pool.variety = VarietyParams(
            genre=genre_variety, year=year_variety, popularity=popularity_variety,
        )

        _apply_year_window(pool, year_variety, target_count)

        await self._enrich_with_tags(pool)
        logger.info(
            "seed_pool built: seed=%r source=%s tracks=%d unique_artists=%d tags=%d",
            seed_label, pool.source, len(pool.tracks), len(pool.artists),
            len(pool.artist_tags),
        )
        return pool

    async def _build_uncached(
        self, seed_label: str, target_count: int,
        discovery_rate: float = 0.5, genre_variety: float = 0.5,
    ) -> SeedPool:
        seed_artist = await self._resolve_artist(seed_label)

        if seed_artist is None:
            seed_artist = await self._resolve_via_track(seed_label)

        if seed_artist is not None:
            return await self._build_artist_pool(
                seed_label, seed_artist, target_count, discovery_rate, genre_variety,
            )

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
        discovery_rate: float = 0.5, genre_variety: float = 0.5,
    ) -> SeedPool:
        similar_count = max(1, round(_SIMILAR_ARTIST_FANOUT * discovery_rate))
        seed_views = await self._catalog.get_artist_views(
            seed_artist.apple_music_id,
            top_songs=_SEED_ARTIST_TOP_SONGS,
            similar_artists=similar_count,
        )

        sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

        async def fetch_one(a: CatalogArtist):
            async with sem:
                return await self._catalog.get_artist_views(
                    a.apple_music_id,
                    top_songs=_SIMILAR_ARTIST_TOP_SONGS,
                    similar_artists=0,
                )

        apple_similar = list(seed_views.similar_artists)
        lb_similar = await self._lb_similar_candidates(seed_views.artist, apple_similar)
        candidates = _interleave(apple_similar, lb_similar)

        similar = _genre_gate_similar(candidates, seed_views.artist, genre_variety)
        if len(similar) > _SIMILAR_ARTIST_PICK:
            similar = random.sample(similar, _SIMILAR_ARTIST_PICK)

        related_views = await asyncio.gather(
            *(fetch_one(a) for a in similar),
            return_exceptions=True,
        )

        by_artist: dict[str, list[CatalogTrack]] = defaultdict(list)
        artists_seen: dict[str, CatalogArtist] = {}

        artists_seen[seed_artist.apple_music_id] = seed_views.artist
        seed_songs = list(seed_views.top_songs)
        if len(seed_songs) > _SEED_ARTIST_POOL_CAP:
            seed_songs = random.sample(seed_songs, _SEED_ARTIST_POOL_CAP)
        for t in seed_songs:
            by_artist[seed_artist.apple_music_id].append(t)

        for v in related_views:
            if isinstance(v, BaseException):
                continue
            if not v.artist.apple_music_id:
                continue
            artists_seen.setdefault(v.artist.apple_music_id, v.artist)
            songs = list(v.top_songs)
            if len(songs) > _DEFAULT_PER_ARTIST_POOL_CAP:
                songs = random.sample(songs, _DEFAULT_PER_ARTIST_POOL_CAP)
            for t in songs:
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

    # ── ListenBrainz similar-artist blending ──────────────────────────

    async def _artist_mbid(self, artist_name: str) -> str | None:
        if self._graph is not None:
            link = await self._graph.get_link(artist_name)
            if link is not None:
                return link.mbid
        if self._mb is None or not hasattr(self._mb, "get_artist_mbid"):
            return None
        mbid = await self._mb.get_artist_mbid(artist_name)
        if self._graph is not None:
            await self._graph.upsert_link(artist_name, mbid=mbid)
        return mbid

    async def _lb_similar_candidates(
        self, seed_artist: CatalogArtist, known: list[CatalogArtist],
    ) -> list[CatalogArtist]:
        """Similar artists from ListenBrainz, resolved to Apple catalog artists.

        Every failure path returns [] so the pool degrades to Apple-only.
        """
        if self._lb is None or self._graph is None:
            return []
        try:
            mbid = await self._artist_mbid(seed_artist.name)
            if not mbid:
                return []

            entries = await self._graph.get_similar(mbid)
            if entries is None:
                fetched = await self._lb.get_similar_artists(mbid)
                entries = [
                    {"artist_mbid": a.mbid, "name": a.name, "score": a.score}
                    for a in fetched
                ]
                await self._graph.put_similar(mbid, entries)

            known_names = {a.name.lower().strip() for a in known}
            known_names.add(seed_artist.name.lower().strip())

            resolved: list[CatalogArtist] = []
            budget = _LB_RESOLVE_BUDGET
            for e in entries[:_LB_SIMILAR_TAKE]:
                name = str(e.get("name") or "").strip()
                if not name or name.lower() in known_names:
                    continue
                link = await self._graph.get_link(name)
                if link is not None and link.apple_music_id:
                    resolved.append(CatalogArtist(
                        apple_music_id=link.apple_music_id, name=link.name or name,
                    ))
                    continue
                if link is not None and link.apple_music_id is None and link.mbid:
                    # Known artist, previous Apple resolution missed — skip quietly.
                    continue
                if budget <= 0:
                    continue
                budget -= 1
                matches = await self._catalog.search_artists(name, limit=1)
                apple = matches[0] if matches else None
                await self._graph.upsert_link(
                    name,
                    mbid=str(e.get("artist_mbid") or "") or None,
                    apple_music_id=apple.apple_music_id if apple else None,
                )
                if apple:
                    resolved.append(apple)
            if resolved:
                logger.info(
                    "ListenBrainz blend: +%d similar artists for %r",
                    len(resolved), seed_artist.name,
                )
            return resolved
        except Exception:
            logger.warning(
                "ListenBrainz blend failed for %r — Apple-only pool",
                seed_artist.name, exc_info=True,
            )
            return []

    # ── Pre-parse: extract structured intent from descriptive prompts ──

    async def _maybe_pre_parse(
        self, seed_label: str, existing_artists: list[str],
    ) -> ParsedSource | None:
        words = seed_label.split()
        if len(words) <= 3 and not existing_artists:
            return None
        return await self._pre_parse_source(seed_label)

    async def _pre_parse_source(self, music_source: str) -> ParsedSource:
        system = (
            "You extract structured music information from a radio station description.\n"
            "Output ONLY this JSON:\n"
            '{"artists":["..."],"albums":["..."],"tracks":["..."],'
            '"style":"...","constraints":"..."}\n'
            "Rules:\n"
            "- artists: specific artist/band names mentioned or strongly implied\n"
            "- albums: specific album names mentioned\n"
            "- tracks: specific song titles mentioned\n"
            "- style: short genre/mood/era phrase (e.g. 'chill indie folk')\n"
            "- constraints: things to avoid (e.g. 'nothing too upbeat')\n"
            "- Use empty list/string if nothing fits. Only extract what's clearly there.\n"
            "No commentary."
        )
        user = f"Station description: {music_source}"
        try:
            raw = await self._llm.complete(
                messages=[LLMMessage("system", system), LLMMessage("user", user)],
                temperature=0.3, max_tokens=1500,
            )
            obj = json.loads(_extract_json_object(raw))
        except Exception:
            logger.warning("Pre-parse failed for %r", music_source[:100])
            return ParsedSource()

        return ParsedSource(
            artists=[n for n in (obj.get("artists") or []) if isinstance(n, str) and n.strip()],
            albums=[n for n in (obj.get("albums") or []) if isinstance(n, str) and n.strip()],
            tracks=[n for n in (obj.get("tracks") or []) if isinstance(n, str) and n.strip()],
            style=str(obj.get("style") or "").strip(),
            constraints=str(obj.get("constraints") or "").strip(),
        )

    # ── Multi-artist pool ─────────────────────────────────────────────

    _MULTI_SIMILAR_SONGS = 4

    async def _build_multi_artist_pool(
        self, seed_label: str, artist_names: list[str], target_count: int,
        discovery_rate: float = 0.5, genre_variety: float = 0.5,
    ) -> SeedPool:
        resolved: list[CatalogArtist] = []
        for name in artist_names:
            a = await self._resolve_artist(name)
            if a:
                resolved.append(a)
            else:
                logger.info("multi-artist: could not resolve %r", name)

        if not resolved:
            return SeedPool(
                seed_label=seed_label, seed_artist=None,
                tracks=[], artists=[], source="none",
            )

        if len(resolved) == 1:
            return await self._build_artist_pool(
                seed_label, resolved[0], target_count, discovery_rate, genre_variety,
            )

        n_seeds = len(resolved)
        top_per_seed = 6 if n_seeds >= 10 else 8
        similar_per_seed = round(4 * discovery_rate)

        sem = asyncio.Semaphore(_FETCH_CONCURRENCY)
        by_artist: dict[str, list[CatalogTrack]] = defaultdict(list)
        artists_seen: dict[str, CatalogArtist] = {}

        async def fetch_seed(a: CatalogArtist):
            async with sem:
                return await self._catalog.get_artist_views(
                    a.apple_music_id,
                    top_songs=top_per_seed,
                    similar_artists=similar_per_seed,
                )

        seed_views = await asyncio.gather(
            *(fetch_seed(a) for a in resolved), return_exceptions=True,
        )

        # Build dominant genre set from seed artists for filtering similar artists.
        from collections import Counter
        genre_counts: Counter[str] = Counter()
        seed_count = 0
        for v in seed_views:
            if isinstance(v, BaseException):
                continue
            seed_count += 1
            for g in (v.artist.genres or ()):
                genre_counts[g.lower()] += 1
        # Dominance threshold scales with genre_variety: 0 = strict (30% of
        # seeds), 0.5 = the historical 15%, >= 0.95 disables the filter.
        if genre_variety >= 0.95:
            dominant_genres: set[str] = set()
        else:
            threshold = max(1, seed_count * 0.3 * (1 - genre_variety))
            dominant_genres = {g for g, c in genre_counts.items() if c >= threshold}

        similar_to_fetch: list[CatalogArtist] = []
        for v in seed_views:
            if isinstance(v, BaseException):
                continue
            artists_seen[v.artist.apple_music_id] = v.artist
            songs = list(v.top_songs)
            if len(songs) > top_per_seed:
                songs = random.sample(songs, top_per_seed)
            for t in songs:
                by_artist[v.artist.apple_music_id].append(t)
            if similar_per_seed > 0:
                for sa in v.similar_artists[:similar_per_seed]:
                    if sa.apple_music_id in artists_seen:
                        continue
                    if dominant_genres and sa.genres:
                        sa_genres = {g.lower() for g in sa.genres}
                        if not sa_genres & dominant_genres:
                            logger.debug(
                                "multi-artist: filtered %s (genres %s outside %s)",
                                sa.name, sa.genres, dominant_genres,
                            )
                            continue
                    similar_to_fetch.append(sa)

        if similar_to_fetch:
            async def fetch_similar(a: CatalogArtist):
                async with sem:
                    return await self._catalog.get_artist_views(
                        a.apple_music_id,
                        top_songs=self._MULTI_SIMILAR_SONGS,
                        similar_artists=0,
                    )

            sim_views = await asyncio.gather(
                *(fetch_similar(a) for a in similar_to_fetch[:16]),
                return_exceptions=True,
            )
            for v in sim_views:
                if isinstance(v, BaseException) or not v.artist.apple_music_id:
                    continue
                artists_seen.setdefault(v.artist.apple_music_id, v.artist)
                songs = list(v.top_songs)
                if len(songs) > self._MULTI_SIMILAR_SONGS:
                    songs = random.sample(songs, self._MULTI_SIMILAR_SONGS)
                for t in songs:
                    by_artist[v.artist.apple_music_id].append(t)

        logger.info(
            "multi-artist pool: %d seeds, %d total artists, %d tracks "
            "(similar_per_seed=%d, dominant_genres=%s, filtered_similar=%d)",
            len(resolved), len(artists_seen),
            sum(len(ts) for ts in by_artist.values()), similar_per_seed,
            dominant_genres,
            len(similar_to_fetch) - sum(
                1 for a in similar_to_fetch if a.apple_music_id in artists_seen
            ),
        )

        return self._assemble_pool(
            seed_label=seed_label,
            seed_artist=resolved[0] if len(resolved) == 1 else None,
            by_artist=by_artist,
            artists_seen=artists_seen,
            source="multi_artist_seed",
        )

    # ── Explicit track / album injection ──────────────────────────────

    async def _add_explicit_tracks(
        self, pool: SeedPool, track_names: list[str],
    ) -> None:
        seen = {t.apple_music_id for t in pool.tracks if t.apple_music_id}
        for name in track_names:
            try:
                results = await self._catalog.search(name, limit=3)
            except Exception:
                logger.warning("Catalog search failed for explicit track %r", name)
                continue
            if results:
                t = results[0]
                if t.apple_music_id and t.apple_music_id not in seen:
                    pool.tracks.insert(0, t)
                    seen.add(t.apple_music_id)

    async def _add_album_tracks(
        self, pool: SeedPool, album_names: list[str],
    ) -> None:
        seen = {t.apple_music_id for t in pool.tracks if t.apple_music_id}
        for name in album_names:
            try:
                results = await self._catalog.search(name, limit=10)
            except Exception:
                logger.warning("Catalog search failed for album %r", name)
                continue
            for t in results:
                if t.apple_music_id and t.apple_music_id not in seen:
                    if name.lower() in t.album.lower() or t.album.lower() in name.lower():
                        pool.tracks.append(t)
                        seen.add(t.apple_music_id)

    # ── Tag enrichment ──────────────────────────────────────────────────

    async def _enrich_with_tags(self, pool: SeedPool) -> None:
        for a in pool.artists:
            key = a.name.lower().strip()
            if a.genres:
                pool.artist_tags[key] = a.genres

        if self._mb is None:
            return

        use_links = (
            self._graph is not None
            and hasattr(self._mb, "get_artist_tags_by_mbid")
        )

        seen_keys: set[str] = set()
        for a in pool.artists:
            key = a.name.lower().strip()
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if use_links:
                mbid = await self._artist_mbid(a.name)
                mb_tags = await self._mb.get_artist_tags_by_mbid(mbid) if mbid else ()
            else:
                mb_tags = await self._mb.get_artist_tags(a.name)
            if mb_tags:
                existing = set(pool.artist_tags.get(key, ()))
                merged = list(mb_tags) + [
                    g for g in existing
                    if g.lower() not in {t.lower() for t in mb_tags}
                ]
                pool.artist_tags[key] = tuple(merged[:10])

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
                temperature=0.4, max_tokens=1500,
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

        # Round-robin interleave then shuffle so the LLM sees a different
        # ordering each run, breaking positional bias in index selection.
        ordered: list[CatalogTrack] = []
        keys = list(cleaned.keys())
        idx = 0
        while any(cleaned[k] for k in keys):
            k = keys[idx % len(keys)]
            if cleaned[k]:
                ordered.append(cleaned[k].pop(0))
            idx += 1
        random.shuffle(ordered)

        artists = [artists_seen[k] for k in artists_seen]

        return SeedPool(
            seed_label=seed_label,
            seed_artist=seed_artist,
            tracks=ordered,
            artists=artists,
            source=source,
        )


def _interleave(a: list[CatalogArtist], b: list[CatalogArtist]) -> list[CatalogArtist]:
    """Alternate two candidate lists, dropping duplicates by Apple ID."""
    out: list[CatalogArtist] = []
    seen: set[str] = set()
    for i in range(max(len(a), len(b))):
        for src in (a, b):
            if i < len(src):
                artist = src[i]
                if artist.apple_music_id and artist.apple_music_id in seen:
                    continue
                if artist.apple_music_id:
                    seen.add(artist.apple_music_id)
                out.append(artist)
    return out


def _genre_gate_similar(
    similar: list[CatalogArtist], seed: CatalogArtist, genre_variety: float,
) -> list[CatalogArtist]:
    """Probabilistically drop similar artists with zero genre overlap with the seed.

    Keep probability equals genre_variety; artists without genre data always pass.
    """
    if genre_variety >= 0.95:
        return similar
    seed_genres = {g.lower() for g in (seed.genres or ())}
    if not seed_genres:
        return similar
    kept: list[CatalogArtist] = []
    for a in similar:
        a_genres = {g.lower() for g in (a.genres or ())}
        if a_genres and not (a_genres & seed_genres) and random.random() > genre_variety:
            logger.debug("genre gate dropped %s (no overlap with seed)", a.name)
            continue
        kept.append(a)
    return kept


_YEAR_FILTER_MAX = 0.4


def _apply_year_window(pool: SeedPool, year_variety: float, target_count: int) -> None:
    """Below _YEAR_FILTER_MAX, trim tracks far from the seed era in place.

    Window is ±(5 + 62·y) years around the median year of the seed artist's
    tracks (all tracks when there is no seed artist). Never trims the pool
    below max(3×target, 24); the closest out-of-window tracks survive first.
    Tracks without a parseable year always pass.
    """
    if year_variety >= _YEAR_FILTER_MAX or not pool.tracks:
        return

    def year_of(t: CatalogTrack) -> int | None:
        try:
            return int(str(t.year)[:4])
        except (TypeError, ValueError):
            return None

    seed_name = pool.seed_artist.name.lower().strip() if pool.seed_artist else None
    anchor_years = [
        y for t in pool.tracks
        if (y := year_of(t)) is not None
        and (seed_name is None or t.artist.lower().strip() == seed_name)
    ]
    if len(anchor_years) < 3:
        anchor_years = [y for t in pool.tracks if (y := year_of(t)) is not None]
    if not anchor_years:
        return

    anchor_years.sort()
    median = anchor_years[len(anchor_years) // 2]
    half_width = 5 + round(62 * year_variety)
    floor = max(target_count * 3, 24)

    def distance(t: CatalogTrack) -> int:
        y = year_of(t)
        return 0 if y is None else max(0, abs(y - median) - half_width)

    out_of_window = sorted(
        (t for t in pool.tracks if distance(t) > 0), key=distance,
    )
    n_keepable = max(0, floor - (len(pool.tracks) - len(out_of_window)))
    survivors = {id(t) for t in out_of_window[:n_keepable]}
    before = len(pool.tracks)
    pool.tracks = [
        t for t in pool.tracks if distance(t) == 0 or id(t) in survivors
    ]
    if len(pool.tracks) < before:
        logger.info(
            "year window ±%d around %d dropped %d/%d tracks (year_variety=%.2f)",
            half_width, median, before - len(pool.tracks), before, year_variety,
        )


def _dedup_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        key = n.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(n.strip())
    return out


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_object(text: str) -> str:
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        raise ValueError(f"no JSON object found in: {text[:200]!r}")
    return m.group(0)
