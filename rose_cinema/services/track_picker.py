from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from rose_cinema.providers import LLMMessage, LLMProvider
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

    def __init__(self, llm: LLMProvider):
        self._llm = llm

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
        return songs
