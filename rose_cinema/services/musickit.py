from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx
import jwt

from rose_cinema.config import settings
from rose_cinema.services.catalog import CatalogTrack, MusicCatalog

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


class MusicKitCatalog(MusicCatalog):
    """Apple Music catalog search via the MusicKit REST API."""

    def __init__(self, signer: MusicKitTokenSigner, storefront: str = "us"):
        self._signer = signer
        self._storefront = storefront

    async def search(self, query: str, limit: int = 5) -> list[CatalogTrack]:
        url = f"{_API_BASE}/catalog/{self._storefront}/search"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                params={"term": query, "types": "songs", "limit": limit},
                headers={"Authorization": f"Bearer {self._signer.token()}"},
            )
        if resp.status_code != 200:
            logger.warning("MusicKit search %r -> %d: %s", query, resp.status_code, resp.text[:200])
            return []
        songs = resp.json().get("results", {}).get("songs", {}).get("data", [])
        out: list[CatalogTrack] = []
        for s in songs:
            attrs = s.get("attributes", {})
            duration_ms = attrs.get("durationInMillis", 0) or 0
            out.append(
                CatalogTrack(
                    apple_music_id=s.get("id", ""),
                    title=attrs.get("name", ""),
                    artist=attrs.get("artistName", ""),
                    album=attrs.get("albumName", ""),
                    year=(attrs.get("releaseDate") or "")[:4],
                    duration_secs=duration_ms / 1000.0,
                )
            )
        return out


def get_music_catalog() -> MusicCatalog | None:
    """Build a MusicKitCatalog from settings, or return None if not configured."""
    cfg = settings
    if not (cfg.musickit_team_id and cfg.musickit_key_id):
        return None
    if cfg.musickit_private_key:
        private_key = cfg.musickit_private_key
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
