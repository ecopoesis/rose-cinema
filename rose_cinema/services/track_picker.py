from __future__ import annotations

import json
import logging
import re

from rose_cinema.providers import LLMMessage, LLMProvider
from rose_cinema.services.catalog import CatalogTrack, MusicCatalog
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


class TrackPicker:
    """Use the LLM to assemble a tracklist seeded by a station's music_source."""

    def __init__(self, llm: LLMProvider, catalog: MusicCatalog | None = None):
        self._llm = llm
        self._catalog = catalog

    async def pick(
        self,
        music_source: str,
        target_minutes: int,
        avg_song_secs: int = 220,
    ) -> list[SongMetadata]:
        target_count = max(3, round((target_minutes * 60) / avg_song_secs))

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
            "Do not invent songs that do not exist. Do not add commentary."
        )
        user = (
            f"Seed: {music_source}\n"
            f"Target: about {target_minutes} minutes of music ({target_count} songs).\n"
            f"Return the JSON array now."
        )

        raw = await self._llm.complete(
            messages=[LLMMessage("system", system), LLMMessage("user", user)],
            temperature=0.6,
            max_tokens=2000,
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
            verified.append(
                SongMetadata(
                    title=best.title,
                    artist=best.artist,
                    album=best.album,
                    year=best.year,
                    apple_music_id=best.apple_music_id,
                    duration_secs=best.duration_secs or p.duration_secs,
                )
            )
        if not verified:
            raise ValueError("All proposed tracks failed catalog verification")
        return verified


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
