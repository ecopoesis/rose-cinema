from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from rose_cinema.config import settings
from rose_cinema.services import apple_auth, apple_music_stream
from rose_cinema.services.apple_auth import AppleMusicAuth, AppleMusicAuthError, auth_status
from rose_cinema.services.apple_music_stream import AppleMusicStreamer
from rose_cinema.services.music_assistant import MusicAssistantClient


def _auth(expires_in: timedelta, invalid: bool = False) -> AppleMusicAuth:
    now = datetime.now(timezone.utc)
    return AppleMusicAuth(
        user_token="tok",
        issued_at=now - timedelta(days=1),
        expires_at=now + expires_in,
        invalid_at=now if invalid else None,
    )


def test_status_without_token_warns():
    assert auth_status(None) == {
        "authorized": False, "expires_at": None, "expired": False, "warn": True,
        "warn_days": settings.apple_music_expiry_warn_days,
    }


def test_status_far_from_expiry_does_not_warn(monkeypatch):
    monkeypatch.setattr(settings, "apple_music_expiry_warn_days", 7)
    s = auth_status(_auth(timedelta(days=30)))
    assert s["authorized"] and not s["expired"] and not s["warn"]


def test_status_inside_warn_window(monkeypatch):
    monkeypatch.setattr(settings, "apple_music_expiry_warn_days", 7)
    s = auth_status(_auth(timedelta(days=6)))
    assert s["warn"] and not s["expired"]


def test_warn_window_is_configurable(monkeypatch):
    monkeypatch.setattr(settings, "apple_music_expiry_warn_days", 14)
    assert auth_status(_auth(timedelta(days=10)))["warn"]


def test_past_expiry_is_expired():
    s = auth_status(_auth(timedelta(days=-1)))
    assert s["expired"] and s["warn"]


def test_rejected_token_is_expired_before_its_date():
    s = auth_status(_auth(timedelta(days=100), invalid=True))
    assert s["expired"] and s["warn"]


def test_json_roundtrip():
    a = _auth(timedelta(days=5), invalid=True)
    assert AppleMusicAuth.from_json(json.loads(json.dumps(a.to_json()))) == a


def _patch_playback(monkeypatch, payload: dict) -> list[dict]:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(200, json=payload)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        apple_music_stream.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(apple_music_stream, "get_musickit_token", lambda: "dev")
    return seen


async def test_streamer_reads_token_per_request(monkeypatch):
    seen = _patch_playback(monkeypatch, {"failureType": "9999"})
    tokens = iter(["first", "second"])

    async def provider():
        return next(tokens)

    streamer = AppleMusicStreamer(token_provider=provider)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await streamer.get_stream_info("1")
    assert [h["media-user-token"] for h in seen] == ["first", "second"]


async def test_streamer_without_token_raises_auth_error():
    async def provider():
        return ""

    with pytest.raises(AppleMusicAuthError):
        await AppleMusicStreamer(token_provider=provider).get_stream_info("1")


async def test_expired_token_is_marked_invalid(monkeypatch):
    _patch_playback(monkeypatch, {
        "failureType": "2034",
        "customerMessage": "MZCommerce.ExpiredPasswordTokenNoDialog_message",
    })
    marked: list[str] = []

    async def fake_mark(token: str) -> None:
        marked.append(token)

    monkeypatch.setattr(apple_music_stream, "mark_apple_auth_invalid", fake_mark)

    async def provider():
        return "stale"

    with pytest.raises(AppleMusicAuthError):
        await AppleMusicStreamer(token_provider=provider).get_stream_info("1")
    assert marked == ["stale"]


async def test_ma_token_push_skips_masked_values(monkeypatch):
    saved: list[dict] = []

    async def fake_call(ws, ids, command, **args):
        if command == "config/providers":
            return {"result": [
                {"domain": "apple_music", "instance_id": "am1"},
                {"domain": "spotify", "instance_id": "sp1"},
            ]}
        if command == "config/providers/get":
            return {"result": {"values": {
                "log_level": {"value": "GLOBAL"},
                "music_user_token": {"value": "this_value_is_encrypted"},
                "music_app_token": {"value": "this_value_is_encrypted"},
            }}}
        if command == "config/providers/save":
            saved.append(args)
            return {"result": {}}
        raise AssertionError(command)

    class _Session:
        async def __aenter__(self):
            return None, None

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr("rose_cinema.services.music_assistant._call", fake_call)
    client = MusicAssistantClient("http://ma", "t")
    monkeypatch.setattr(client, "_session", lambda: _Session())

    assert await client.set_apple_music_tokens("app", "user") == 1
    assert saved == [{
        "provider_domain": "apple_music",
        "instance_id": "am1",
        "values": {
            "log_level": "GLOBAL",
            "music_app_token": "app",
            "music_user_manual_token": "user",
        },
    }]


def _patch_storefront(monkeypatch, status_code: int) -> None:
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        apple_auth.httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(
            lambda request: httpx.Response(status_code, json={}),
        )),
    )


@pytest.mark.parametrize("code,expected", [(200, True), (401, False), (403, False)])
async def test_verify_user_token(monkeypatch, code, expected):
    _patch_storefront(monkeypatch, code)
    assert await apple_auth.verify_user_token("u", "d") is expected


async def test_verify_user_token_outage_is_not_a_rejection(monkeypatch):
    _patch_storefront(monkeypatch, 503)
    with pytest.raises(httpx.HTTPStatusError):
        await apple_auth.verify_user_token("u", "d")


@pytest.mark.parametrize("invalid,expected", [(False, "tok"), (True, "")])
async def test_rejected_token_is_not_handed_out(monkeypatch, invalid, expected):
    class _Session:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    async def fake_load(session):
        return _auth(timedelta(days=30), invalid=invalid)

    monkeypatch.setattr("rose_cinema.database.async_session", lambda: _Session())
    monkeypatch.setattr(apple_auth, "load_apple_auth", fake_load)
    assert await apple_auth.get_apple_music_user_token() == expected
