from __future__ import annotations

import asyncio
import json
import time

import pytest
import httpx

from rose_cinema.services.musicbrainz import MusicBrainzClient, _normalize_name, _MB_API


class TestNormalizeName:
    def test_strips_the(self):
        assert _normalize_name("The National") == "national"

    def test_strips_a(self):
        assert _normalize_name("A Perfect Circle") == "perfect circle"

    def test_lowercases(self):
        assert _normalize_name("Mazzy Star") == "mazzy star"

    def test_strips_whitespace(self):
        assert _normalize_name("  Palace ") == "palace"


class TestMusicBrainzClient:
    @pytest.fixture
    def search_response(self):
        return {
            "artists": [
                {"id": "mbid-palace", "name": "Palace", "score": 100},
                {"id": "mbid-other", "name": "Palace Music", "score": 80},
            ]
        }

    @pytest.fixture
    def tags_response(self):
        return {
            "id": "mbid-palace",
            "name": "Palace",
            "tags": [
                {"name": "indie rock", "count": 5},
                {"name": "shoegaze", "count": 3},
                {"name": "dream pop", "count": 2},
                {"name": "noise", "count": 0},
            ],
            "genres": [
                {"name": "indie rock", "count": 4},
                {"name": "post-punk", "count": 1},
            ],
        }

    @pytest.mark.asyncio
    async def test_get_artist_tags_returns_sorted_by_count(
        self, search_response, tags_response,
    ):
        client = MusicBrainzClient("test/1.0")
        responses = [
            httpx.Response(200, json=search_response),
            httpx.Response(200, json=tags_response),
        ]
        client._client = httpx.AsyncClient(
            base_url=_MB_API,
            transport=httpx.MockTransport(lambda req: responses.pop(0)),
        )
        client._last_request = 0.0

        tags = await client.get_artist_tags("Palace")

        assert "indie rock" in tags
        assert "shoegaze" in tags
        assert "dream pop" in tags
        assert "post-punk" in tags
        assert "noise" not in tags
        assert tags[0] == "indie rock"

    @pytest.mark.asyncio
    async def test_search_caches_name_to_mbid(self, search_response):
        call_count = 0

        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=search_response)

        client = MusicBrainzClient("test/1.0")
        client._client = httpx.AsyncClient(
            base_url=_MB_API, transport=httpx.MockTransport(handler),
        )
        client._last_request = 0.0

        mbid1 = await client._search_artist("Palace")
        mbid2 = await client._search_artist("Palace")

        assert mbid1 == mbid2 == "mbid-palace"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_lookup_caches_mbid_to_tags(self, tags_response):
        call_count = 0

        def handler(req):
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=tags_response)

        client = MusicBrainzClient("test/1.0")
        client._client = httpx.AsyncClient(
            base_url=_MB_API, transport=httpx.MockTransport(handler),
        )
        client._last_request = 0.0

        tags1 = await client._lookup_tags("mbid-palace")
        tags2 = await client._lookup_tags("mbid-palace")

        assert tags1 == tags2
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self):
        client = MusicBrainzClient("test/1.0")
        client._client = httpx.AsyncClient(
            base_url=_MB_API,
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, json={"artists": [
                    {"id": "mbid-x", "name": "Something Completely Different"},
                ]})
            ),
        )
        client._last_request = 0.0

        tags = await client.get_artist_tags("Palace")
        assert tags == ()

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        client = MusicBrainzClient("test/1.0")
        client._client = httpx.AsyncClient(
            base_url=_MB_API,
            transport=httpx.MockTransport(lambda req: httpx.Response(500)),
        )
        client._last_request = 0.0

        tags = await client.get_artist_tags("Palace")
        assert tags == ()
