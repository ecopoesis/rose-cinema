from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rose_cinema.config import settings
from rose_cinema.repositories.sql import (
    SqlListenPositionRepository,
    SqlPlaylistRunRepository,
    SqlStationRepository,
)
from rose_cinema.repositories import ListenPositionRecord
from rose_cinema.services.music_assistant import get_music_assistant_client
from rose_cinema.services.playlist_items import build_ma_media_items
from rose_cinema.services.slimproto_client import SlimProtoClient

logger = logging.getLogger(__name__)

HEALTH_INTERVAL = 10
POSITION_INTERVAL = 15
IDLE_TIMEOUT = 300


def _stable_mac(key: str) -> bytes:
    h = hashlib.md5(key.encode()).digest()
    mac = bytearray(h[:6])
    mac[0] = (mac[0] & 0xFE) | 0x02  # locally administered, unicast
    return bytes(mac)


@dataclass
class StreamInfo:
    station_id: str
    uid: str
    slug: str
    player_name: str
    mount: str
    run_id: str
    entry_index: int = 0
    slim_client: SlimProtoClient | None = None
    ffmpeg_proc: asyncio.subprocess.Process | None = None
    feed_task: asyncio.Task | None = None
    started_at: datetime | None = None
    active_listeners: int = 0
    last_listener_at: datetime | None = None


def _stream_key(station_id: str, uid: str) -> str:
    return f"{station_id}:{uid}"


class StreamManager:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory
        self._streams: dict[str, StreamInfo] = {}
        self._health_task: asyncio.Task | None = None
        self._position_task: asyncio.Task | None = None
        self._idle_task: asyncio.Task | None = None

    async def start_stream(
        self,
        station_id: str,
        uid: str,
        slug: str,
        run_id: str,
        playlist_json: str,
        station_name: str,
        dj_name: str,
        base_url: str,
        art_url: str | None,
        episode: int | None,
        entry_index: int = 0,
    ) -> StreamInfo:
        key = _stream_key(station_id, uid)
        existing = self._streams.get(key)
        if existing and existing.slim_client and existing.slim_client._connected:
            return existing

        player_name = f"rc-{slug}"[:15]
        mount = f"/{player_name}"

        parsed = urlparse(settings.ma_url)
        ma_host = parsed.hostname or "127.0.0.1"

        slim = SlimProtoClient(
            server=ma_host,
            port=3483,
            player_name=player_name,
            mac=_stable_mac(key),
        )
        await slim.connect()

        icecast_url = (
            f"icecast://source:{settings.icecast_source_password}"
            f"@{settings.icecast_host}:{settings.icecast_port}{mount}"
        )
        ff_proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-f", "s32le", "-ar", "48000", "-ac", "2", "-i", "pipe:0",
            "-c:a", "libmp3lame", "-b:a", "192k",
            "-f", "mp3", icecast_url,
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        feed_task = asyncio.create_task(self._feed_ffmpeg(slim, ff_proc, player_name))

        info = StreamInfo(
            station_id=station_id,
            uid=uid,
            slug=slug,
            player_name=player_name,
            mount=mount,
            run_id=run_id,
            entry_index=entry_index,
            slim_client=slim,
            ffmpeg_proc=ff_proc,
            feed_task=feed_task,
            started_at=datetime.now(timezone.utc),
            last_listener_at=datetime.now(timezone.utc),
        )
        self._streams[key] = info

        asyncio.create_task(self._drain_stderr(ff_proc, f"ffmpeg[{player_name}]"))
        asyncio.create_task(self._wait_and_play(info, playlist_json, station_name, dj_name, base_url, art_url, episode))

        return info

    async def stop_stream(self, station_id: str, uid: str) -> None:
        key = _stream_key(station_id, uid)
        info = self._streams.pop(key, None)
        if not info:
            return
        await self._save_position(info)
        await self._kill_stream(info)

    def get_stream(self, station_id: str, uid: str) -> StreamInfo | None:
        key = _stream_key(station_id, uid)
        info = self._streams.get(key)
        if info and info.slim_client and not info.slim_client._connected:
            return None
        return info

    def listener_connect(self, station_id: str, uid: str) -> None:
        key = _stream_key(station_id, uid)
        info = self._streams.get(key)
        if info:
            info.active_listeners += 1
            info.last_listener_at = datetime.now(timezone.utc)

    def listener_disconnect(self, station_id: str, uid: str) -> None:
        key = _stream_key(station_id, uid)
        info = self._streams.get(key)
        if info:
            info.active_listeners = max(0, info.active_listeners - 1)
            if info.active_listeners == 0:
                info.last_listener_at = datetime.now(timezone.utc)

    async def start_background_tasks(self) -> None:
        self._health_task = asyncio.create_task(self._health_loop())
        self._position_task = asyncio.create_task(self._position_loop())
        self._idle_task = asyncio.create_task(self._idle_loop())

    async def shutdown(self) -> None:
        for task in (self._health_task, self._position_task, self._idle_task):
            if task:
                task.cancel()
        for key in list(self._streams):
            sid, uid = key.split(":", 1)
            await self.stop_stream(sid, uid)

    # ── internals ─────────────────────────────────────────────────────

    async def _wait_and_play(
        self,
        info: StreamInfo,
        playlist_json: str,
        station_name: str,
        dj_name: str,
        base_url: str,
        art_url: str | None,
        episode: int | None,
    ) -> None:
        client = get_music_assistant_client()
        if not client:
            logger.error("MA client unavailable, cannot start playback")
            return

        for _ in range(60):
            try:
                players = await client.list_players()
                if any(p.display_name == info.player_name or p.player_id == info.player_name for p in players):
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)
        else:
            logger.warning("SlimProto player %s did not appear in MA within 30s", info.player_name)

        items = build_ma_media_items(
            playlist_json,
            station_name=station_name,
            dj_name=dj_name,
            base_url=base_url,
            art_url=art_url,
            episode=episode,
            start_index=info.entry_index,
        )
        if not items:
            logger.warning("No playable items for stream %s:%s", info.station_id, info.uid)
            return

        # find the player_id MA assigned (may differ from player_name)
        player_id = info.player_name
        try:
            players = await client.list_players()
            for p in players:
                if p.display_name == info.player_name:
                    player_id = p.player_id
                    break
        except Exception:
            pass

        try:
            await client.play_media(player_id=player_id, media=items, option="replace")
            logger.info("Started playback on %s (%d items from index %d)", player_id, len(items), info.entry_index)
        except Exception:
            logger.exception("Failed to start playback on %s", player_id)

    async def _feed_ffmpeg(self, slim: SlimProtoClient, ff: asyncio.subprocess.Process, label: str) -> None:
        try:
            async for chunk in slim.audio_chunks():
                if ff.stdin and not ff.stdin.is_closing():
                    ff.stdin.write(chunk)
                    await ff.stdin.drain()
                else:
                    break
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception:
            logger.debug("Feed error for %s", label, exc_info=True)
        finally:
            if ff.stdin and not ff.stdin.is_closing():
                try:
                    ff.stdin.close()
                except Exception:
                    pass

    async def _drain_stderr(self, proc: asyncio.subprocess.Process, label: str) -> None:
        assert proc.stderr is not None
        async for line in proc.stderr:
            text = line.decode(errors="replace").rstrip()
            if text:
                logger.debug("[%s] %s", label, text)

    async def _kill_stream(self, info: StreamInfo) -> None:
        if info.slim_client:
            await info.slim_client.disconnect()
        if info.feed_task and not info.feed_task.done():
            info.feed_task.cancel()
        if info.ffmpeg_proc and info.ffmpeg_proc.returncode is None:
            info.ffmpeg_proc.terminate()
            try:
                await asyncio.wait_for(info.ffmpeg_proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                info.ffmpeg_proc.kill()

    async def _save_position(self, info: StreamInfo) -> None:
        try:
            async with self._sf() as session:
                repo = SqlListenPositionRepository(session)
                await repo.upsert_position(ListenPositionRecord(
                    station_id=info.station_id,
                    uid=info.uid,
                    run_id=info.run_id,
                    entry_index=info.entry_index,
                    listened_date=date.today().isoformat(),
                ))
        except Exception:
            logger.exception("Failed to save listen position for %s:%s", info.station_id, info.uid)

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(HEALTH_INTERVAL)
            for key, info in list(self._streams.items()):
                slim_dead = info.slim_client and not info.slim_client._connected
                ff_dead = info.ffmpeg_proc and info.ffmpeg_proc.returncode is not None
                if slim_dead or ff_dead:
                    logger.warning("Stream %s has dead component (slim=%s ff=%s), cleaning up", key, slim_dead, ff_dead)
                    await self._save_position(info)
                    await self._kill_stream(info)
                    self._streams.pop(key, None)

    async def _position_loop(self) -> None:
        while True:
            await asyncio.sleep(POSITION_INTERVAL)
            client = get_music_assistant_client()
            if not client:
                continue
            for key, info in list(self._streams.items()):
                try:
                    state = await client.get_player_state(info.player_name)
                    if state.get("state") == "idle":
                        await self._handle_playlist_end(info)
                    elif state.get("current_item"):
                        await self._update_position_from_state(info, state)
                except Exception:
                    logger.debug("Position poll failed for %s", key, exc_info=True)

    async def _idle_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            now = datetime.now(timezone.utc)
            for key, info in list(self._streams.items()):
                if info.active_listeners == 0 and info.last_listener_at:
                    idle_secs = (now - info.last_listener_at).total_seconds()
                    if idle_secs > IDLE_TIMEOUT:
                        logger.info("Stream %s idle for %ds, stopping", key, int(idle_secs))
                        sid, uid = key.split(":", 1)
                        await self.stop_stream(sid, uid)

    async def _update_position_from_state(self, info: StreamInfo, state: dict) -> None:
        current = state.get("current_item")
        if not current:
            return
        # MA returns the current item's URI — try to match it to an entry index
        uri = current.get("uri", "") if isinstance(current, dict) else ""
        if uri:
            async with self._sf() as session:
                repo = SqlListenPositionRepository(session)
                await repo.upsert_position(ListenPositionRecord(
                    station_id=info.station_id,
                    uid=info.uid,
                    run_id=info.run_id,
                    entry_index=info.entry_index,
                    listened_date=date.today().isoformat(),
                ))

    async def _handle_playlist_end(self, info: StreamInfo) -> None:
        logger.info("Playlist ended for %s:%s (run %s), looking for fallback", info.station_id, info.uid, info.run_id)
        try:
            async with self._sf() as session:
                run_repo = SqlPlaylistRunRepository(session)
                runs = await run_repo.list_by_station(info.station_id)
                ready_runs = [r for r in runs if r.status in ("ready", "saved")]

                current_idx = next((i for i, r in enumerate(ready_runs) if r.id == info.run_id), -1)
                if current_idx >= 0 and current_idx + 1 < len(ready_runs):
                    next_run = ready_runs[current_idx + 1]
                    info.run_id = next_run.id
                    info.entry_index = 0

                    station_repo = SqlStationRepository(session)
                    station = await station_repo.get(info.station_id)
                    dj_name = "?"
                    art_url = None
                    base_url = settings.public_base_url.rstrip("/")
                    if station and station.dj_id:
                        from rose_cinema.repositories.sql import SqlDJRepository
                        dj_repo = SqlDJRepository(session)
                        dj = await dj_repo.get(station.dj_id)
                        if dj:
                            dj_name = dj.name

                    await self._wait_and_play(
                        info, next_run.playlist_json,
                        station_name=station.name if station else "",
                        dj_name=dj_name,
                        base_url=base_url,
                        art_url=art_url,
                        episode=next_run.episode,
                    )
                    await self._save_position(info)
                    logger.info("Fell back to run %s for %s:%s", next_run.id, info.station_id, info.uid)
                else:
                    logger.info("No more runs for %s:%s, stopping stream", info.station_id, info.uid)
                    key = _stream_key(info.station_id, info.uid)
                    await self._save_position(info)
                    await self._kill_stream(info)
                    self._streams.pop(key, None)
        except Exception:
            logger.exception("Fallback handling failed for %s:%s", info.station_id, info.uid)
