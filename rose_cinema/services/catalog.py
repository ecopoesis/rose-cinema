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
