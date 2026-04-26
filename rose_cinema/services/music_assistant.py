from __future__ import annotations

import itertools
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

import websockets

from rose_cinema.config import settings

logger = logging.getLogger(__name__)


@dataclass
class MAPlayer:
    player_id: str
    display_name: str
    available: bool
    type: str


class MusicAssistantClient:
    """One-shot WebSocket client for Music Assistant.

    Each public method opens a fresh WS, authenticates, sends commands,
    and disconnects. We filter responses by message_id and ignore
    push events / partial frames we didn't ask for.
    """

    def __init__(self, url: str, token: str):
        self._ws_url = (
            url.rstrip("/")
            .replace("https://", "wss://")
            .replace("http://", "ws://")
            + "/ws"
        )
        self._token = token

    async def list_players(self) -> list[MAPlayer]:
        async with self._session() as (ws, mid):
            r = await _call(ws, mid, "players/all")
        return [
            MAPlayer(
                player_id=p.get("player_id", ""),
                display_name=p.get("display_name") or "?",
                available=bool(p.get("available")),
                type=p.get("type") or "",
            )
            for p in (r.get("result") or [])
        ]

    async def play_media(
        self,
        player_id: str,
        media_uris: list[str],
        option: str = "replace",
    ) -> None:
        if not media_uris:
            raise ValueError("play_media requires at least one URI")
        async with self._session() as (ws, mid):
            r = await _call(
                ws, mid, "player_queues/play_media",
                queue_id=player_id,
                media=media_uris,
                option=option,
            )
        if r.get("error"):
            raise RuntimeError(f"MA play_media failed: {r['error']}")

    @asynccontextmanager
    async def _session(self):
        ids = itertools.count(1)
        async with websockets.connect(self._ws_url, max_size=2**24) as ws:
            await ws.recv()  # server_info pushed on connect
            auth = await _call(ws, ids, "auth", token=self._token)
            if not (auth.get("result") or {}).get("authenticated"):
                raise RuntimeError(f"MA auth failed: {auth!r}")
            yield ws, ids


async def _call(ws, ids, command: str, **args) -> dict:
    msg_id = f"rc-{next(ids)}"
    await ws.send(json.dumps({"message_id": msg_id, "command": command, "args": args}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("message_id") == msg_id:
            return msg


def get_music_assistant_client() -> MusicAssistantClient | None:
    if not (settings.ma_url and settings.ma_token):
        return None
    return MusicAssistantClient(settings.ma_url, settings.ma_token)
