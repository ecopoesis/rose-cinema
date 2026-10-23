from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from rose_cinema.config import settings

logger = logging.getLogger(__name__)

APPLE_MUSIC_AUTH_KEY = "apple_music_auth"

# Apple's music user tokens are opaque and carry no expiry; they're good for
# about six months (Music Assistant makes the same assumption).
USER_TOKEN_LIFETIME = timedelta(days=180)


class AppleMusicAuthError(RuntimeError):
    """Apple rejected the music user token (expired or revoked)."""


@dataclass
class AppleMusicAuth:
    user_token: str
    issued_at: datetime
    expires_at: datetime
    invalid_at: datetime | None = None

    @property
    def expired(self) -> bool:
        return self.invalid_at is not None or _now() >= self.expires_at

    def to_json(self) -> dict:
        return {
            "user_token": self.user_token,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "invalid_at": self.invalid_at.isoformat() if self.invalid_at else None,
        }

    @classmethod
    def from_json(cls, value: dict) -> AppleMusicAuth:
        invalid_at = value.get("invalid_at")
        return cls(
            user_token=value["user_token"],
            issued_at=datetime.fromisoformat(value["issued_at"]),
            expires_at=datetime.fromisoformat(value["expires_at"]),
            invalid_at=datetime.fromisoformat(invalid_at) if invalid_at else None,
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def load_apple_auth(session) -> AppleMusicAuth | None:
    from rose_cinema.repositories.sql import SqlAppSettingsRepository

    value = await SqlAppSettingsRepository(session).get(APPLE_MUSIC_AUTH_KEY)
    if not isinstance(value, dict) or not value.get("user_token"):
        return None
    return AppleMusicAuth.from_json(value)


async def save_apple_auth(session, user_token: str) -> AppleMusicAuth:
    from rose_cinema.repositories.sql import SqlAppSettingsRepository

    now = _now()
    auth = AppleMusicAuth(
        user_token=user_token, issued_at=now, expires_at=now + USER_TOKEN_LIFETIME,
    )
    await SqlAppSettingsRepository(session).set(APPLE_MUSIC_AUTH_KEY, auth.to_json())
    return auth


async def get_apple_music_user_token() -> str:
    """Current music user token from the DB, read per call so a re-auth takes
    effect without a restart. Empty when none is stored or Apple has rejected
    it; the 180-day date is only an estimate, so it doesn't gate use."""
    from rose_cinema.database import async_session

    async with async_session() as session:
        auth = await load_apple_auth(session)
    return auth.user_token if auth and not auth.invalid_at else ""


async def has_apple_music_auth() -> bool:
    return bool(await get_apple_music_user_token())


async def mark_apple_auth_invalid(user_token: str) -> None:
    """Record that Apple rejected this token, unless it has since been replaced.

    One conditional UPDATE, so a re-auth committed meanwhile can't be overwritten.
    """
    from sqlalchemy import text

    from rose_cinema.database import async_session

    async with async_session() as session:
        result = await session.execute(
            text(
                "UPDATE app_settings"
                " SET value = jsonb_set(value, '{invalid_at}', to_jsonb(CAST(:now AS text))),"
                " updated_at = now()"
                " WHERE key = :key AND value->>'user_token' = :token"
                " AND value->>'invalid_at' IS NULL"
            ),
            {"now": _now().isoformat(), "key": APPLE_MUSIC_AUTH_KEY, "token": user_token},
        )
        await session.commit()
    if result.rowcount:
        logger.warning("Apple Music user token rejected by Apple; re-auth required")


def auth_status(auth: AppleMusicAuth | None) -> dict:
    warn_days = settings.apple_music_expiry_warn_days
    if not auth:
        return {"authorized": False, "expires_at": None, "expired": False,
                "warn": True, "warn_days": warn_days}
    return {
        "authorized": True,
        "expires_at": auth.expires_at.isoformat(),
        "expired": auth.expired,
        "warn": auth.expired or (auth.expires_at - _now()) < timedelta(days=warn_days),
        "warn_days": warn_days,
    }


async def verify_user_token(user_token: str, developer_token: str) -> bool:
    """True if Apple accepts the token, False if it rejects it (401/403).
    Other failures raise httpx.HTTPStatusError rather than read as a rejection."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            "https://api.music.apple.com/v1/me/storefront",
            headers={
                "authorization": f"Bearer {developer_token}",
                "music-user-token": user_token,
            },
        )
    if resp.status_code in (401, 403):
        return False
    resp.raise_for_status()
    return True
