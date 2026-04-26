from __future__ import annotations

from rose_cinema.providers import LLMMessage, LLMProvider
from rose_cinema.services.catalog import (
    CatalogArtist,
    CatalogArtistViews,
    CatalogTrack,
    MusicCatalog,
)


class FakeLLM(LLMProvider):
    """Replays canned responses in order; raises if asked for one that wasn't queued."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[list[LLMMessage]] = []

    async def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.8,
        max_tokens: int = 500,
    ) -> str:
        self.calls.append(messages)
        if not self._responses:
            raise RuntimeError("FakeLLM out of canned responses")
        return self._responses.pop(0)


class FakeCatalog(MusicCatalog):
    """In-memory MusicCatalog. Set the dicts in __init__ to whatever fixtures you want."""

    def __init__(
        self,
        *,
        songs_by_query: dict[str, list[CatalogTrack]] | None = None,
        artists_by_query: dict[str, list[CatalogArtist]] | None = None,
        artist_views: dict[str, CatalogArtistViews] | None = None,
        genres: dict[str, str] | None = None,
        genre_top_songs: dict[str, list[CatalogTrack]] | None = None,
    ):
        self.songs_by_query = songs_by_query or {}
        self.artists_by_query = artists_by_query or {}
        self.artist_views = artist_views or {}
        self.genres = genres or {}
        self.genre_top_songs = genre_top_songs or {}
        self.calls: list[tuple] = []

    async def search(self, query: str, limit: int = 5) -> list[CatalogTrack]:
        self.calls.append(("search", query))
        return list(self.songs_by_query.get(query.lower(), []))[:limit]

    async def search_artists(self, query: str, limit: int = 5) -> list[CatalogArtist]:
        self.calls.append(("search_artists", query))
        return list(self.artists_by_query.get(query.lower(), []))[:limit]

    async def get_artist_views(
        self,
        artist_id: str,
        *,
        top_songs: int = 10,
        similar_artists: int = 10,
    ) -> CatalogArtistViews:
        self.calls.append(("get_artist_views", artist_id, top_songs, similar_artists))
        v = self.artist_views.get(artist_id)
        if v is None:
            return CatalogArtistViews(
                artist=CatalogArtist(apple_music_id=artist_id, name="?"),
            )
        return CatalogArtistViews(
            artist=v.artist,
            top_songs=v.top_songs[:top_songs] if top_songs else [],
            similar_artists=v.similar_artists[:similar_artists] if similar_artists else [],
        )

    async def list_genres(self) -> dict[str, str]:
        return dict(self.genres)

    async def get_genre_top_songs(self, genre_id: str, limit: int = 30) -> list[CatalogTrack]:
        self.calls.append(("get_genre_top_songs", genre_id))
        return list(self.genre_top_songs.get(genre_id, []))[:limit]


# ── builders ───────────────────────────────────────────────────────────


def mk_track(track_id: str, title: str, artist: str, year: str = "2020") -> CatalogTrack:
    return CatalogTrack(
        apple_music_id=track_id,
        title=title,
        artist=artist,
        album=f"{title} Album",
        year=year,
        duration_secs=210.0,
    )


def mk_artist(artist_id: str, name: str, *genres: str) -> CatalogArtist:
    return CatalogArtist(apple_music_id=artist_id, name=name, genres=tuple(genres))
