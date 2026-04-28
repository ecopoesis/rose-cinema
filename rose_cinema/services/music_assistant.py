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
        media: list[str | dict],
        option: str = "replace",
    ) -> None:
        if not media:
            raise ValueError("play_media requires at least one item")
        async with self._session() as (ws, mid):
            r = await _call(
                ws, mid, "player_queues/play_media",
                queue_id=player_id,
                media=media,
                option=option,
            )
        if r.get("error"):
            raise RuntimeError(f"MA play_media failed: {r['error']}")

    async def remove_playlist(self, playlist_id: str) -> None:
        async with self._session() as (ws, mid):
            r = await _call(
                ws, mid, "music/playlists/remove_playlist",
                db_playlist_id=playlist_id,
            )
            if r.get("error"):
                raise RuntimeError(f"MA remove_playlist failed: {r['error']}")

    async def save_as_playlist(
        self,
        name: str,
        apple_music_uris: list[str],
        dj_mp3_metadata: list[dict],
        ordered_uris: list[str],
        art_url: str | None = None,
    ) -> dict:
        """Create an MA playlist with mixed Apple Music + DJ MP3 entries.

        Apple Music tracks already use library-friendly URIs; DJ MP3s get
        ingested via the builtin provider first to obtain a library:// URI,
        then the full ordered URI list is written to a fresh playlist.
        """
        async with self._session() as (ws, mid):
            # 1. Ingest each DJ MP3 into MA's library; record the
            #    canonical URI we get back so we can substitute it into
            #    the ordered list.
            url_to_uri: dict[str, str] = {}
            for item in dj_mp3_metadata:
                url = item["uri"]
                # Strip images before add — MA may try to validate them
                # during ingest, which can fail and block the whole save.
                add_item = {k: v for k, v in item.items() if k != "metadata"}
                add_item["metadata"] = {
                    k: v for k, v in (item.get("metadata") or {}).items()
                    if k != "images"
                }
                r = await _call(ws, mid, "music/library/add_item", item=add_item)
                result = r.get("result") or {}
                lib_uri = result.get("uri")
                if not lib_uri:
                    raise RuntimeError(f"MA add_item({url}) returned no URI: {r!r}")
                url_to_uri[url] = lib_uri

                # Set album art via a separate update call (non-fatal).
                if art_url:
                    track_id = result.get("item_id")
                    if track_id:
                        try:
                            r_upd = await _call(
                                ws, mid, "music/tracks/update",
                                item_id=str(track_id),
                                update={
                                    "item_id": str(track_id),
                                    "provider": "library",
                                    "media_type": "track",
                                    "name": result.get("name", ""),
                                    "uri": lib_uri,
                                    "provider_mappings": [
                                        {
                                            "item_id": url,
                                            "provider_domain": "builtin",
                                            "provider_instance": "builtin",
                                            "available": True,
                                        }
                                    ],
                                    "metadata": {
                                        "images": [{
                                            "type": "thumb",
                                            "path": art_url,
                                            "provider": "builtin",
                                            "remotely_accessible": True,
                                        }]
                                    },
                                },
                            )
                            if r_upd.get("error"):
                                logger.warning("MA track artwork update failed: %s", r_upd["error"])
                        except Exception:
                            logger.warning("Failed to set artwork for DJ track %s, continuing", url)

            final_uris = [url_to_uri.get(u, u) for u in ordered_uris]

            # 2. Create the playlist (provider=builtin so it's MA-managed).
            r = await _call(ws, mid, "music/playlists/create_playlist",
                            name=name, provider_instance_or_domain="builtin")
            pl = r.get("result") or {}
            pid = pl.get("item_id")
            if not pid:
                raise RuntimeError(f"MA create_playlist returned no item_id: {r!r}")

            # 3. Append all items.
            r = await _call(ws, mid, "music/playlists/add_playlist_tracks",
                            db_playlist_id=pid, uris=final_uris)
            # The add is async on MA's side; we don't block on it here.

        return {
            "playlist_id": pid,
            "playlist_uri": pl.get("uri"),
            "playlist_name": pl.get("name"),
            "queued_uris": len(final_uris),
        }

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
