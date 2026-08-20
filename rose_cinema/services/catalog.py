from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class CatalogTrack:
    apple_music_id: str
    title: str
    artist: str
    album: str = ""
    year: str = ""
    duration_secs: float = 0.0
    track_number: int = 0


@dataclass
class CatalogArtist:
    apple_music_id: str
    name: str
    genres: tuple[str, ...] = ()


@dataclass
class CatalogArtistViews:
    artist: CatalogArtist
    top_songs: list[CatalogTrack] = field(default_factory=list)
    similar_artists: list[CatalogArtist] = field(default_factory=list)


def best_match(title: str, artist: str, results: list[CatalogTrack]) -> CatalogTrack | None:
    """First result whose title and artist mutually substring-match the proposal."""
    ptitle = title.lower()
    partist = artist.lower()
    for r in results:
        rtitle = r.title.lower()
        rartist = r.artist.lower()
        title_ok = ptitle in rtitle or rtitle in ptitle
        artist_ok = partist in rartist or rartist in partist
        if title_ok and artist_ok:
            return r
    return None


class MusicCatalog(ABC):
    """Abstraction over a music catalog: discovery (search/charts/similar) + verification."""

    @abstractmethod
    async def search(self, query: str, limit: int = 5) -> list[CatalogTrack]: ...

    @abstractmethod
    async def search_artists(self, query: str, limit: int = 5) -> list[CatalogArtist]: ...

    @abstractmethod
    async def get_artist_views(
        self,
        artist_id: str,
        *,
        top_songs: int = 10,
        similar_artists: int = 10,
    ) -> CatalogArtistViews: ...

    @abstractmethod
    async def list_genres(self) -> dict[str, str]: ...

    @abstractmethod
    async def get_genre_top_songs(self, genre_id: str, limit: int = 30) -> list[CatalogTrack]: ...

    @abstractmethod
    async def get_song(self, song_id: str) -> dict: ...
