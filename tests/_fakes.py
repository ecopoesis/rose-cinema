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
        songs_by_id: dict[str, dict] | None = None,
    ):
        self.songs_by_query = songs_by_query or {}
        self.artists_by_query = artists_by_query or {}
        self.artist_views = artist_views or {}
        self.genres = genres or {}
        self.genre_top_songs = genre_top_songs or {}
        self.songs_by_id = songs_by_id or {}
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

    async def get_song(self, song_id: str) -> dict:
        self.calls.append(("get_song", song_id))
        return dict(self.songs_by_id.get(song_id, {}))

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


class FakeMusicBrainz:

    def __init__(self, tags_by_name: dict[str, tuple[str, ...]] | None = None):
        self._tags = tags_by_name or {}
        self.calls: list[str] = []

    async def get_artist_tags(self, artist_name: str) -> tuple[str, ...]:
        self.calls.append(artist_name)
        return self._tags.get(artist_name.lower().strip(), ())


class FakeListenBrainz:
    """Canned similar-artists lists keyed by MBID."""

    def __init__(self, similar_by_mbid: dict[str, list] | None = None):
        self._similar = similar_by_mbid or {}
        self.calls: list[str] = []

    async def get_similar_artists(self, artist_mbid: str):
        self.calls.append(artist_mbid)
        return list(self._similar.get(artist_mbid, []))


class RaisingListenBrainz:

    async def get_similar_artists(self, artist_mbid: str):
        raise RuntimeError("LB down")


class FakeGraphStore:
    """In-memory stand-in for ArtistGraphStore."""

    def __init__(self):
        from rose_cinema.repositories import ArtistLinkRecord
        self._record_cls = ArtistLinkRecord
        self.links: dict = {}
        self.similar: dict[str, list[dict]] = {}
        self.resolutions: dict = {}

    async def get_link(self, artist_name: str):
        return self.links.get(artist_name.lower().strip())

    async def upsert_link(self, artist_name: str, *, mbid=None, apple_music_id=None):
        key = artist_name.lower().strip()
        existing = self.links.get(key)
        if existing:
            mbid = mbid or existing.mbid
            apple_music_id = apple_music_id or existing.apple_music_id
        self.links[key] = self._record_cls(
            name_key=key, name=artist_name.strip(),
            mbid=mbid, apple_music_id=apple_music_id,
        )

    async def get_similar(self, artist_mbid: str):
        return self.similar.get(artist_mbid)

    async def put_similar(self, artist_mbid: str, payload: list[dict]):
        self.similar[artist_mbid] = list(payload)

    async def get_resolutions(self, recording_mbids: list[str]):
        return {m: self.resolutions[m] for m in recording_mbids if m in self.resolutions}

    async def put_resolution(self, record):
        self.resolutions[record.recording_mbid] = record


class FakeMirror:
    """Canned MusicBrainz-mirror discographies keyed by artist MBID."""

    def __init__(
        self,
        mbid_by_name: dict[str, str] | None = None,
        discography_by_mbid: dict[str, list] | None = None,
    ):
        self._mbids = mbid_by_name or {}
        self._disco = discography_by_mbid or {}
        self.calls: list[tuple] = []

    async def get_artist_mbid(self, name: str):
        self.calls.append(("get_artist_mbid", name))
        return self._mbids.get(name.lower().strip())

    async def get_discography(self, artist_mbid: str, limit: int = 200):
        self.calls.append(("get_discography", artist_mbid))
        return list(self._disco.get(artist_mbid, []))[:limit]
