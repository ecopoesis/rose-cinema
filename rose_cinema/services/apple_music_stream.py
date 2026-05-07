from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from dataclasses import dataclass

import httpx
import m3u8
from pywidevine import PSSH, Cdm, Device, DeviceTypes
from pywidevine.license_protocol_pb2 import WidevinePsshData

from rose_cinema.config import settings
from rose_cinema.services.musickit import _get_token as get_musickit_token

logger = logging.getLogger(__name__)

PLAYBACK_URL = "https://play.music.apple.com/WebObjects/MZPlay.woa/wa/webPlayback"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    " (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
)


@dataclass
class TrackStreamInfo:
    stream_url: str
    decryption_key: str | None
    is_encrypted: bool


class AppleMusicStreamer:
    def __init__(self) -> None:
        self._user_token = settings.apple_music_user_token
        self._device: Device | None = None
        self._key_cache: dict[str, str] = {}

    def _load_device(self) -> Device:
        if self._device:
            return self._device
        cdm_dir = settings.widevine_cdm_dir
        with open(os.path.join(cdm_dir, "client_id.bin"), "rb") as f:
            client_id = f.read()
        with open(os.path.join(cdm_dir, "private_key.pem"), "rb") as f:
            private_key = f.read()
        self._device = Device(
            client_id=client_id,
            private_key=private_key,
            type_=DeviceTypes.ANDROID,
            security_level=3,
            flags={},
        )
        return self._device

    def _get_headers(self) -> dict[str, str]:
        app_token = get_musickit_token()
        return {
            "authorization": f"Bearer {app_token}",
            "media-user-token": self._user_token,
            "connection": "keep-alive",
            "accept": "application/json",
            "origin": "https://music.apple.com",
            "referer": "https://music.apple.com/",
            "accept-encoding": "gzip, deflate, br",
            "content-type": "application/json;charset=utf-8",
            "user-agent": USER_AGENT,
        }

    async def get_stream_info(self, apple_music_id: str) -> TrackStreamInfo:
        headers = self._get_headers()
        data = {"salableAdamId": apple_music_id}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(PLAYBACK_URL, headers=headers, json=data)
            resp.raise_for_status()
            content = resp.json()

        if content.get("failureType"):
            raise RuntimeError(f"Playback metadata failed: {content.get('failureMessage')}")

        song = content["songList"][0]
        license_url = song.get("hls-key-server-url")
        assets = song.get("assets", [])

        ctrp256 = [a for a in assets if a.get("flavor") == "28:ctrp256"]
        if not ctrp256:
            raise RuntimeError(f"No ctrp256 asset for track {apple_music_id}")

        playlist_url = ctrp256[0]["URL"]
        stream_url, key_uri = await self._parse_m3u8(playlist_url)

        if not key_uri:
            return TrackStreamInfo(stream_url=stream_url, decryption_key=None, is_encrypted=False)

        key_id = base64.b64decode(key_uri.split(",")[1])
        dec_key = await self._get_decryption_key(license_url, key_id, key_uri, apple_music_id)
        return TrackStreamInfo(stream_url=stream_url, decryption_key=dec_key, is_encrypted=True)

    async def _parse_m3u8(self, playlist_url: str) -> tuple[str, str | None]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(playlist_url)
            resp.raise_for_status()
            playlist_text = resp.text

        parsed = m3u8.loads(playlist_text)
        if not parsed.segments:
            raise RuntimeError("Empty m3u8 playlist")

        segment = parsed.segments[0]
        base_path = playlist_url.rsplit("/", 1)[0]
        track_url = base_path + "/" + segment.uri

        key_uri = None
        if segment.key and segment.key.uri:
            key_uri = segment.key.uri

        return track_url, key_uri

    async def _get_decryption_key(
        self, license_url: str, key_id: bytes, uri: str, item_id: str
    ) -> str:
        if item_id in self._key_cache:
            return self._key_cache[item_id]

        pssh_data = WidevinePsshData()
        pssh_data.algorithm = 1
        pssh_data.key_ids.append(key_id)
        init_data = base64.b64encode(pssh_data.SerializeToString()).decode()
        pssh = PSSH.new(system_id=PSSH.SystemId.Widevine, init_data=init_data)

        device = self._load_device()
        cdm = Cdm.from_device(device)
        session_id = cdm.open()
        challenge = cdm.get_license_challenge(session_id, pssh)

        challenge_b64 = base64.b64encode(challenge).decode()
        license_data = {
            "challenge": challenge_b64,
            "key-system": "com.widevine.alpha",
            "uri": uri,
            "adamId": item_id,
            "isLibrary": False,
            "user-initiated": True,
        }

        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            resp = await client.post(
                license_url,
                content=json.dumps(license_data),
                headers=self._get_headers(),
            )
            resp.raise_for_status()
            license_resp = resp.json()

        track_license = license_resp.get("license")
        if not track_license:
            raise RuntimeError(f"No license returned for {item_id}")

        cdm.parse_license(session_id, track_license)
        key = next(k for k in cdm.get_keys(session_id) if k.type == "CONTENT")
        cdm.close(session_id)

        hex_key = key.key.hex()
        self._key_cache[item_id] = hex_key
        logger.info("Got decryption key for track %s", item_id)
        return hex_key

    async def stream_track_as_mp3(self, apple_music_id: str):
        info = await self.get_stream_info(apple_music_id)

        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
        if info.is_encrypted and info.decryption_key:
            cmd += ["-decryption_key", info.decryption_key]
        cmd += ["-re", "-i", info.stream_url]
        cmd += ["-ar", "44100", "-ac", "2", "-c:a", "libmp3lame", "-b:a", settings.native_stream_bitrate,
                "-write_xing", "0", "-reservoir", "0", "-id3v2_version", "0", "-f", "mp3", "pipe:1"]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            while True:
                chunk = await proc.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
            stderr = await proc.stderr.read()
            if stderr and proc.returncode != 0:
                logger.warning("ffmpeg stderr for %s: %s", apple_music_id, stderr.decode(errors="replace")[:500])
