from __future__ import annotations

import json
import logging
import re

from rose_cinema.providers import LLMMessage, LLMProvider
from rose_cinema.services.catalog import CatalogTrack, MusicCatalog
from rose_cinema.services.seed_pool import SeedPool, SeedPoolBuilder
from rose_cinema.services.station_builder import SongMetadata

logger = logging.getLogger(__name__)


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json_array(text: str) -> str:
    m = _JSON_FENCE_RE.search(text)
    if m:
        text = m.group(1)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON array found in LLM output: {text[:200]!r}")
    return text[start : end + 1]


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_object(text: str) -> str:
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        raise ValueError(f"no JSON object in: {text[:200]!r}")
    return m.group(0)


class TrackPicker:
    """Assemble a tracklist from a station's music_source.

    Two modes:
      1. Pool mode (preferred): SeedPoolBuilder produces a candidate pool of
         catalog-grounded real tracks; the LLM curates indices from it.
      2. Legacy LLM-discovery mode (fallback): LLM proposes tracks from its
         training, optional MusicCatalog verifies them. Used when the seed
         doesn't resolve into any catalog grounding.
    """

    def __init__(
        self,
        llm: LLMProvider,
        catalog: MusicCatalog | None = None,
        seed_builder: SeedPoolBuilder | None = None,
    ):
        self._llm = llm
        self._catalog = catalog
        self._seed_builder = seed_builder

    async def pick(
        self,
        music_source: str,
        target_minutes: int,
        avg_song_secs: int = 220,
        exclude_ids: set[str] | None = None,
        source_artists: list[str] | None = None,
        source_albums: list[str] | None = None,
        source_tracks: list[str] | None = None,
        discovery_rate: float = 0.5,
    ) -> list[SongMetadata]:
        target_count = max(3, round((target_minutes * 60) / avg_song_secs))

        if self._seed_builder is not None:
            pool = await self._seed_builder.build(
                music_source, target_count,
                source_artists=source_artists,
                source_albums=source_albums,
                source_tracks=source_tracks,
                discovery_rate=discovery_rate,
            )
            if pool.tracks:
                if exclude_ids:
                    before = len(pool.tracks)
                    pool.tracks = [t for t in pool.tracks if t.apple_music_id not in exclude_ids]
                    logger.info("Excluded %d recently-played tracks from pool", before - len(pool.tracks))
                return await self._pick_from_pool(pool, music_source, target_count)
            logger.info("seed_pool empty for %r — falling back to LLM-discovery", music_source)

        return await self._pick_from_llm_discovery(music_source, target_count)

    # ── Pool-curation path ─────────────────────────────────────────────

    async def _pick_from_pool(
        self,
        pool: SeedPool,
        music_source: str,
        target_count: int,
    ) -> list[SongMetadata]:
        artist_tags = dict(pool.artist_tags)
        for a in pool.artists:
            key = a.name.lower().strip()
            if key not in artist_tags and a.genres:
                artist_tags[key] = a.genres

        listing = "\n".join(
            _format_pool_line(i, t, artist_tags)
            for i, t in enumerate(pool.tracks)
        )

        seed_hint = (
            f" The seed artist is {pool.seed_artist.name}; cap at 2 picks from them."
            if pool.seed_artist else ""
        )
        style_rule = (
            f"- Style target: {pool.style_hint}. Prioritize tracks that match this feel.\n"
            if pool.style_hint else ""
        )
        constraint_rule = (
            f"- Constraint: {pool.constraints}.\n"
            if pool.constraints else ""
        )

        system = (
            f"You curate a {target_count}-track radio set from a fixed numbered pool.\n"
            f"Output ONLY this JSON: {{\"picks\":[<int>,<int>,...]}} with exactly {target_count} indices.\n"
            "RULES:\n"
            f"- Indices must be in [0, {len(pool.tracks) - 1}].\n"
            "- Same artist no more than twice across the picks.\n"
            f"-{seed_hint}\n"
            "- Tags in {{}} show genre/style. Prefer tracks whose tags align with the seed's musical identity.\n"
            f"{style_rule}"
            f"{constraint_rule}"
            "- Span eras: when the pool offers a year range, prefer mixing.\n"
            "- Order for arc: opener with energy, mid section deeper cuts, closer that resolves.\n"
            "No commentary, no extra keys."
        )
        user = (
            f"Seed: {music_source}\n\n"
            f"Pool:\n{listing}\n\n"
            f"Pick exactly {target_count} indices."
        )

        raw = await self._llm.complete(
            messages=[LLMMessage("system", system), LLMMessage("user", user)],
            temperature=0.85,
            max_tokens=2000,
        )
        logger.debug("TrackPicker pool curation raw: %s", raw)

        try:
            obj = json.loads(_extract_json_object(raw))
            picks_raw = obj.get("picks") or []
        except Exception:
            logger.warning("LLM curation produced unparsable output: %r", raw[:200])
            picks_raw = []

        seen_ids: set[str] = set()
        picked: list[SongMetadata] = []
        for v in picks_raw:
            try:
                i = int(v)
            except (TypeError, ValueError):
                continue
            if i < 0 or i >= len(pool.tracks):
                continue
            t = pool.tracks[i]
            if t.apple_music_id in seen_ids:
                continue
            seen_ids.add(t.apple_music_id)
            picked.append(_track_to_song(t))

        # Apply the per-artist cap on the LLM's picks first.
        capped = _cap_per_artist(picked, max_per_artist=2)
        # Track what survived so we don't double-count during top-up.
        seen_ids = {s.apple_music_id for s in capped if s.apple_music_id}
        artist_counts: dict[str, int] = {}
        for s in capped:
            k = s.artist.lower().strip()
            artist_counts[k] = artist_counts.get(k, 0) + 1

        # Top up from the round-robin pool order if we came up short.
        if len(capped) < target_count:
            shortfall = target_count - len(capped)
            for t in pool.tracks:
                if shortfall <= 0:
                    break
                if t.apple_music_id and t.apple_music_id in seen_ids:
                    continue
                k = t.artist.lower().strip()
                if artist_counts.get(k, 0) >= 2:
                    continue
                capped.append(_track_to_song(t))
                seen_ids.add(t.apple_music_id)
                artist_counts[k] = artist_counts.get(k, 0) + 1
                shortfall -= 1
            if shortfall > 0:
                logger.info(
                    "Pool top-up couldn't reach target %d — settled at %d (pool size %d)",
                    target_count, len(capped), len(pool.tracks),
                )

        return capped

    # ── Legacy LLM-discovery path (kept for fallback) ─────────────────

    async def _pick_from_llm_discovery(
        self, music_source: str, target_count: int,
    ) -> list[SongMetadata]:
        system = (
            "You are a music programmer for a radio station. Given a seed track, "
            "artist, or theme, produce a coherent tracklist that fits thematically "
            "and totals roughly the target duration.\n\n"
            "VARIETY RULES:\n"
            "- Treat the seed as the starting point of a journey, not the only point.\n"
            "- No single artist should appear more than twice across the whole list.\n"
            "- Span adjacent eras, scenes, or sub-genres that an informed listener "
            "would recognize as kin to the seed (contemporaries, influences, descendants).\n"
            "- The seed track or artist may appear once — at most twice — but not as "
            "every entry.\n\n"
            "OUTPUT FORMAT:\n"
            "Output ONLY a JSON array. Each element must be an object with these "
            'keys: "title", "artist", "album", "year", "duration_secs".\n'
            "- title, artist: required, real songs only\n"
            "- album, year: include if you are confident; otherwise empty string\n"
            "- duration_secs: a reasonable integer (typical 150-360)\n"
            "Do not invent songs that do not exist. Do not add commentary.\n"
            "Output compact single-line JSON with no extra whitespace or code fences."
        )
        user = (
            f"Seed: {music_source}\n"
            f"Target: about {target_count} songs.\n"
            f"Return the JSON array now."
        )

        raw = await self._llm.complete(
            messages=[LLMMessage("system", system), LLMMessage("user", user)],
            temperature=0.6,
            max_tokens=8000,
        )
        logger.debug("TrackPicker raw output: %s", raw)

        payload = _extract_json_array(raw)
        items = json.loads(payload)

        songs: list[SongMetadata] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            artist = (item.get("artist") or "").strip()
            if not title or not artist:
                continue
            songs.append(
                SongMetadata(
                    title=title,
                    artist=artist,
                    album=str(item.get("album") or "").strip(),
                    year=str(item.get("year") or "").strip(),
                    duration_secs=float(item.get("duration_secs") or 210.0),
                )
            )

        if not songs:
            raise ValueError(f"LLM returned no usable tracks: {raw[:200]!r}")

        if self._catalog is not None:
            songs = await self._verify(songs)
        return _cap_per_artist(songs, max_per_artist=2)

    async def _verify(self, proposals: list[SongMetadata]) -> list[SongMetadata]:
        """Drop hallucinated tracks; replace metadata with canonical catalog truth."""
        verified: list[SongMetadata] = []
        for p in proposals:
            try:
                results = await self._catalog.search(f"{p.title} {p.artist}", limit=5)
            except Exception:
                logger.exception("Catalog search failed for %r — %r", p.title, p.artist)
                continue
            best = _best_match(p, results)
            if best is None:
                logger.info("Dropped hallucinated track: %s — %s", p.artist, p.title)
                continue
            verified.append(_track_to_song(best, fallback_duration=p.duration_secs))
        if not verified:
            raise ValueError("All proposed tracks failed catalog verification")
        return verified


def _track_to_song(t: CatalogTrack, *, fallback_duration: float = 0.0) -> SongMetadata:
    return SongMetadata(
        title=t.title,
        artist=t.artist,
        album=t.album,
        year=t.year,
        apple_music_id=t.apple_music_id,
        duration_secs=t.duration_secs or fallback_duration,
    )


def _cap_per_artist(songs: list[SongMetadata], max_per_artist: int) -> list[SongMetadata]:
    counts: dict[str, int] = {}
    out: list[SongMetadata] = []
    for s in songs:
        key = s.artist.lower().strip()
        if counts.get(key, 0) >= max_per_artist:
            logger.info("Capping artist %r at %d — dropped %r", s.artist, max_per_artist, s.title)
            continue
        counts[key] = counts.get(key, 0) + 1
        out.append(s)
    return out


def _format_pool_line(
    i: int, t: CatalogTrack, artist_tags: dict[str, tuple[str, ...]],
) -> str:
    base = f"  {i} | {t.title} — {t.artist} ({t.year or '?'}) [{t.album or '?'}]"
    tags = artist_tags.get(t.artist.lower().strip(), ())
    if tags:
        return f"{base} {{{', '.join(tags)}}}"
    return base


def _best_match(proposal: SongMetadata, results: list[CatalogTrack]) -> CatalogTrack | None:
    ptitle = proposal.title.lower()
    partist = proposal.artist.lower()
    for r in results:
        rtitle = r.title.lower()
        rartist = r.artist.lower()
        title_match = ptitle in rtitle or rtitle in ptitle
        artist_match = partist in rartist or rartist in partist
        if title_match and artist_match:
            return r
    return None
