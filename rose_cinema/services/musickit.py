from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx
import jwt

from rose_cinema.config import settings
from rose_cinema.services.catalog import (
    CatalogArtist,
    CatalogArtistViews,
    CatalogTrack,
    MusicCatalog,
)

logger = logging.getLogger(__name__)

_API_BASE = "https://api.music.apple.com/v1"
_TOKEN_LIFETIME_SECS = 90 * 86_400        # 90 days; Apple max is 6 months
_TOKEN_REFRESH_BEFORE = 1 * 86_400        # refresh if < 24h remaining


class MusicKitTokenSigner:
    """Mints and caches an Apple Music developer JWT (ES256)."""

    def __init__(self, team_id: str, key_id: str, private_key: str):
        self._team_id = team_id
        self._key_id = key_id
        self._private_key = private_key
        self._token: str | None = None
        self._exp: int = 0

    def token(self) -> str:
        now = int(time.time())
        if self._token and (self._exp - now) > _TOKEN_REFRESH_BEFORE:
            return self._token
        exp = now + _TOKEN_LIFETIME_SECS
        payload = {"iss": self._team_id, "iat": now, "exp": exp}
        headers = {"alg": "ES256", "kid": self._key_id}
        self._token = jwt.encode(
            payload, self._private_key, algorithm="ES256", headers=headers
        )
        self._exp = exp
        logger.info("Minted MusicKit JWT (kid=%s, exp=%d)", self._key_id, exp)
        return self._token


def _track_from_payload(item: dict) -> CatalogTrack:
    attrs = item.get("attributes") or {}
    duration_ms = attrs.get("durationInMillis", 0) or 0
    return CatalogTrack(
        apple_music_id=item.get("id", ""),
        title=attrs.get("name", ""),
        artist=attrs.get("artistName", ""),
        album=attrs.get("albumName", ""),
        year=(attrs.get("releaseDate") or "")[:4],
        duration_secs=duration_ms / 1000.0,
        track_number=attrs.get("trackNumber", 0) or 0,
    )


def _artist_from_payload(item: dict) -> CatalogArtist:
    attrs = item.get("attributes") or {}
    genres = attrs.get("genreNames") or []
    return CatalogArtist(
        apple_music_id=item.get("id", ""),
        name=attrs.get("name", ""),
        genres=tuple(genres),
    )


class MusicKitCatalog(MusicCatalog):
    """Apple Music catalog over the MusicKit REST API.

    Uses a developer JWT only (no MusicUserToken needed for catalog reads).
    Single shared httpx.AsyncClient per call — fine at current call volume
    (~13 reqs per generate). Wrap with asyncio.Semaphore at the caller if
    fan-out grows.
    """

    def __init__(self, signer: MusicKitTokenSigner, storefront: str = "us"):
        self._signer = signer
        self._storefront = storefront
        self._genres_cache: dict[str, str] | None = None

    async def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{_API_BASE}{path}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url, params=params,
                headers={"Authorization": f"Bearer {self._signer.token()}"},
            )
        if resp.status_code != 200:
            logger.warning("MusicKit %s %r -> %d: %s", path, params, resp.status_code, resp.text[:200])
            return {}
        return resp.json()

    # ── existing surface ───────────────────────────────────────────────

    async def search(self, query: str, limit: int = 5) -> list[CatalogTrack]:
        body = await self._get(
            f"/catalog/{self._storefront}/search",
            {"term": query, "types": "songs", "limit": limit},
        )
        songs = (body.get("results") or {}).get("songs", {}).get("data", [])
        return [_track_from_payload(s) for s in songs]

    # ── discovery surface ──────────────────────────────────────────────

    async def search_artists(self, query: str, limit: int = 5) -> list[CatalogArtist]:
        body = await self._get(
            f"/catalog/{self._storefront}/search",
            {"term": query, "types": "artists", "limit": limit},
        )
        artists = (body.get("results") or {}).get("artists", {}).get("data", [])
        return [_artist_from_payload(a) for a in artists]

    async def get_artist_views(
        self,
        artist_id: str,
        *,
        top_songs: int = 10,
        similar_artists: int = 10,
    ) -> CatalogArtistViews:
        views = []
        params: dict = {}
        if top_songs > 0:
            views.append("top-songs")
            params["limit[artists:top-songs]"] = top_songs
        if similar_artists > 0:
            views.append("similar-artists")
            params["limit[artists:similar-artists]"] = similar_artists
        if views:
            params["views"] = ",".join(views)

        body = await self._get(f"/catalog/{self._storefront}/artists/{artist_id}", params)
        data = body.get("data") or []
        if not data:
            return CatalogArtistViews(
                artist=CatalogArtist(apple_music_id=artist_id, name=""),
            )
        seed_payload = data[0]
        seed = _artist_from_payload(seed_payload)
        view_data = seed_payload.get("views") or {}
        top = [
            _track_from_payload(t)
            for t in (view_data.get("top-songs", {}).get("data") or [])
        ]
        sim = [
            _artist_from_payload(a)
            for a in (view_data.get("similar-artists", {}).get("data") or [])
        ]
        return CatalogArtistViews(artist=seed, top_songs=top, similar_artists=sim)

    async def list_genres(self) -> dict[str, str]:
        if self._genres_cache is not None:
            return self._genres_cache
        body = await self._get(
            f"/catalog/{self._storefront}/genres", {"limit": 200},
        )
        data = body.get("data") or []
        out: dict[str, str] = {}
        for g in data:
            name = (g.get("attributes") or {}).get("name") or ""
            gid = g.get("id") or ""
            if name and gid:
                out[name] = gid
        self._genres_cache = out
        return out

    async def get_genre_top_songs(self, genre_id: str, limit: int = 30) -> list[CatalogTrack]:
        body = await self._get(
            f"/catalog/{self._storefront}/charts",
            {"types": "songs", "genre": genre_id, "limit": limit},
        )
        results = body.get("results") or {}
        songs_blocks = results.get("songs") or []
        out: list[CatalogTrack] = []
        for block in songs_blocks:
            for item in block.get("data") or []:
                out.append(_track_from_payload(item))
        return out


_token_signer: MusicKitTokenSigner | None = None


def _get_token() -> str:
    """Return a cached MusicKit developer JWT, creating the signer on first call."""
    global _token_signer
    if _token_signer is None:
        cfg = settings
        if not (cfg.musickit_team_id and cfg.musickit_key_id):
            raise RuntimeError("MusicKit not configured (need MUSICKIT_TEAM_ID and MUSICKIT_KEY_ID)")
        if cfg.musickit_private_key:
            private_key = _resolve_inline_key(cfg.musickit_private_key)
        elif cfg.musickit_private_key_path and Path(cfg.musickit_private_key_path).exists():
            private_key = Path(cfg.musickit_private_key_path).read_text()
        else:
            raise RuntimeError("MusicKit private key not configured")
        _token_signer = MusicKitTokenSigner(
            team_id=cfg.musickit_team_id,
            key_id=cfg.musickit_key_id,
            private_key=private_key,
        )
    return _token_signer.token()


def _resolve_inline_key(raw: str) -> str:
    """Accept either raw PEM or base64-encoded PEM (env-friendly single-line form)."""
    s = raw.strip()
    if s.startswith("-----BEGIN"):
        return s
    import base64
    return base64.b64decode(s).decode()


def get_music_catalog() -> MusicCatalog | None:
    """Build a MusicKitCatalog from settings, or return None if not configured."""
    cfg = settings
    if not (cfg.musickit_team_id and cfg.musickit_key_id):
        return None
    if cfg.musickit_private_key:
        try:
            private_key = _resolve_inline_key(cfg.musickit_private_key)
        except Exception:
            logger.exception("Failed to decode MUSICKIT_PRIVATE_KEY (expect raw PEM or base64-encoded PEM)")
            return None
    elif cfg.musickit_private_key_path and Path(cfg.musickit_private_key_path).exists():
        private_key = Path(cfg.musickit_private_key_path).read_text()
    else:
        logger.warning("MusicKit configured but no private key (set MUSICKIT_PRIVATE_KEY or MUSICKIT_PRIVATE_KEY_PATH)")
        return None
    signer = MusicKitTokenSigner(
        team_id=cfg.musickit_team_id,
        key_id=cfg.musickit_key_id,
        private_key=private_key,
    )
    return MusicKitCatalog(signer, storefront=cfg.musickit_storefront)
