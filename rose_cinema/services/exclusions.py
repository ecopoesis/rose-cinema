from __future__ import annotations

import re

EXCLUDED_ARTISTS_KEY = "excluded_artists"

# Conservative separators for multi-artist credit strings ("Future Feat. Drake",
# "Drake & 21 Savage", "KAYTRANADA x Anderson .Paak"). Deliberately excludes
# bare "and"/"+" which appear inside band names.
_ARTIST_SPLIT_RE = re.compile(
    r"\s*,\s*|\s*&\s*|\s*/\s*|\s+x\s+|\s+(?:feat\.?|ft\.?|featuring|with|vs\.?)\s+",
    re.IGNORECASE,
)

# Featured credits hiding in track titles: "Life Is Good (feat. Drake)".
_FEAT_IN_TITLE_RE = re.compile(
    r"[(\[](?:feat\.?|ft\.?|featuring|with)\s+([^)\]]+)[)\]]",
    re.IGNORECASE,
)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def normalize_exclusions(names: list[str] | None) -> set[str]:
    return {_norm(n) for n in (names or []) if n and n.strip()}


def credited_artists(title: str, artist: str) -> set[str]:
    """All artist names credited on a track: the full artist string, each
    segment of a multi-artist credit, and any featured names in the title.

    Segment-exact matching keeps "Nick Drake" safe when "Drake" is excluded.
    """
    names = {_norm(artist)} if artist.strip() else set()
    names |= {_norm(p) for p in _ARTIST_SPLIT_RE.split(artist) if p.strip()}
    for m in _FEAT_IN_TITLE_RE.finditer(title):
        names |= {_norm(p) for p in _ARTIST_SPLIT_RE.split(m.group(1)) if p.strip()}
    return names


def is_excluded(title: str, artist: str, excluded_norm: set[str]) -> bool:
    if not excluded_norm:
        return False
    return bool(credited_artists(title, artist) & excluded_norm)


def merge_exclusions(*lists: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for n in lst or []:
            key = _norm(n)
            if key and key not in seen:
                seen.add(key)
                out.append(n.strip())
    return out


async def get_global_excluded_artists(session) -> list[str]:
    from rose_cinema.repositories.sql import SqlAppSettingsRepository

    value = await SqlAppSettingsRepository(session).get(EXCLUDED_ARTISTS_KEY)
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return []
