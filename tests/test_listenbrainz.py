from __future__ import annotations

import json

import httpx
import pytest

from rose_cinema.services.listenbrainz import ListenBrainzClient


def _client_with(response: httpx.Response) -> ListenBrainzClient:
    client = ListenBrainzClient("https://labs.example", "algo")
    client._client = httpx.AsyncClient(
        base_url="https://labs.example",
        transport=httpx.MockTransport(lambda req: response),
    )
    return client


@pytest.mark.asyncio
async def test_parses_and_sorts_by_score():
    payload = [
        {"artist_mbid": "mb-b", "name": "B", "score": 10},
        {"artist_mbid": "mb-a", "name": "A", "score": 99},
        {"artist_mbid": "", "name": "no mbid", "score": 50},
        {"artist_mbid": "mb-c", "name": "", "score": 50},
    ]
    client = _client_with(httpx.Response(200, content=json.dumps(payload)))

    result = await client.get_similar_artists("mb-seed")

    assert [(a.mbid, a.name, a.score) for a in result] == [
        ("mb-a", "A", 99), ("mb-b", "B", 10),
    ]


@pytest.mark.asyncio
async def test_non_200_returns_empty():
    client = _client_with(httpx.Response(400, content="bad request"))
    assert await client.get_similar_artists("mb-seed") == []


@pytest.mark.asyncio
async def test_malformed_payload_returns_empty():
    client = _client_with(httpx.Response(200, content="not json"))
    assert await client.get_similar_artists("mb-seed") == []
