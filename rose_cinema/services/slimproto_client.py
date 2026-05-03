from __future__ import annotations

import asyncio
import ipaddress
import logging
import struct
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 5


@dataclass
class SlimProtoClient:
    server: str
    port: int
    player_name: str
    mac: bytes = field(default_factory=lambda: b"\x00" * 6)

    _reader: asyncio.StreamReader | None = field(default=None, repr=False)
    _writer: asyncio.StreamWriter | None = field(default=None, repr=False)
    _connected: bool = field(default=False, repr=False)
    _reader_task: asyncio.Task | None = field(default=None, repr=False)
    _heartbeat_task: asyncio.Task | None = field(default=None, repr=False)
    _audio_queue: asyncio.Queue[bytes] = field(default_factory=lambda: asyncio.Queue(64), repr=False)
    _http_task: asyncio.Task | None = field(default=None, repr=False)
    _jiffies: int = field(default=0, repr=False)

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.server, self.port)
        await self._send_helo()
        self._reader_task = asyncio.create_task(self._read_loop())

    async def disconnect(self) -> None:
        self._connected = False
        for task in (self._reader_task, self._heartbeat_task, self._http_task):
            if task and not task.done():
                task.cancel()
        if self._writer and not self._writer.is_closing():
            self._writer.close()
        await self._audio_queue.put(b"")

    async def audio_chunks(self):
        while True:
            chunk = await self._audio_queue.get()
            if not chunk:
                break
            yield chunk

    async def _send_helo(self) -> None:
        caps = (
            "Model=squeezelite,ModelName=SqueezeLite,AccuratePlayPoints=1,"
            "HasDigitalOut=1,HasPolarityInversion=1,Balance=1,"
            f"Firmware=rc-1.0,MaxSampleRate=384000,"
            "CanHTTPS=1,flc,aif,pcm,mp3,aac,ogg,ops,alc"
        )
        data = struct.pack("BB6s", 12, 0, self.mac)
        data += b"\x00" * 16  # UUID
        data += struct.pack("!H", 0)  # wlan channels
        data += struct.pack("!Q", 0)  # bytes received
        data += b"en"  # language
        data += caps.encode()
        await self._send_frame(b"HELO", data)

    async def _send_frame(self, command: bytes, data: bytes) -> None:
        if not self._writer or self._writer.is_closing():
            return
        packet = command + struct.pack("!I", len(data)) + data
        self._writer.write(packet)
        await self._writer.drain()

    async def _read_loop(self) -> None:
        assert self._reader
        buf = b""
        try:
            while True:
                data = await asyncio.wait_for(self._reader.read(4096), timeout=HEARTBEAT_INTERVAL * 3)
                if not data:
                    break
                buf += data
                while len(buf) >= 2:
                    frame_len = struct.unpack("!H", buf[:2])[0]
                    total = frame_len + 2
                    if len(buf) < total:
                        break
                    cmd = buf[2:6]
                    payload = buf[6:total]
                    buf = buf[total:]
                    await self._handle(cmd, payload)
        except (asyncio.TimeoutError, ConnectionResetError, OSError):
            pass
        finally:
            logger.debug("SlimProto read loop ended for %s", self.player_name)
            self._connected = False

    async def _handle(self, cmd: bytes, payload: bytes) -> None:
        tag = cmd.decode(errors="replace").strip().lower()
        if tag == "vers":
            pass
        elif tag == "setd":
            if payload and payload[0] == 0:
                name_data = struct.pack("B", 0) + self.player_name.encode() + b"\x00"
                await self._send_frame(b"SETD", name_data)
            elif payload and payload[0] == 0xFE:
                pass
        elif tag == "strm":
            await self._handle_strm(payload)
        elif tag in ("aude", "audg", "grfb", "grfe", "grfs", "visu", "anic"):
            pass
        else:
            logger.debug("SlimProto unhandled: %s (%d bytes)", tag, len(payload))

        if not self._connected and tag in ("setd", "vers"):
            pass
        if tag == "strm" or (tag == "setd" and not self._connected):
            if not self._heartbeat_task or self._heartbeat_task.done():
                self._connected = True
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _handle_strm(self, data: bytes) -> None:
        if len(data) < 24:
            return
        command = chr(data[0])
        if command == "s":
            port = struct.unpack("!H", data[18:20])[0]
            ip_int = struct.unpack("!L", data[20:24])[0]
            httpreq = data[24:] if len(data) > 24 else b""

            if ip_int == 0:
                host = self.server
            else:
                host = str(ipaddress.IPv4Address(ip_int))

            req_str = httpreq.decode(errors="replace")
            path = ""
            for line in req_str.split("\r\n"):
                if line.startswith("GET "):
                    path = line.split(" ")[1]
                    break

            url = f"http://{host}:{port}{path}"
            logger.info("strm start: %s", url)

            await self._send_stat(b"STMc")
            if self._http_task and not self._http_task.done():
                self._http_task.cancel()
            self._http_task = asyncio.create_task(self._fetch_audio(url))

        elif command == "q":
            logger.debug("strm stop")
            if self._http_task and not self._http_task.done():
                self._http_task.cancel()
        elif command == "f":
            logger.debug("strm flush")
        elif command == "p":
            logger.debug("strm pause")
        elif command == "u":
            logger.debug("strm unpause")

    async def _fetch_audio(self, url: str) -> None:
        await self._send_stat(b"STMc")
        try:
            decoder = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "warning",
                "-f", "flac", "-i", "pipe:0",
                "-f", "s32le", "-ar", "48000", "-ac", "2", "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            async def _read_pcm():
                assert decoder.stdout
                while True:
                    pcm = await decoder.stdout.read(65536)
                    if not pcm:
                        break
                    await self._audio_queue.put(pcm)

            read_task = asyncio.create_task(_read_pcm())

            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=300, write=10, pool=10)) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        logger.error("Audio fetch failed: %d from %s", resp.status_code, url)
                        if decoder.stdin:
                            decoder.stdin.close()
                        await read_task
                        return
                    await self._send_stat(b"STMs")
                    async for chunk in resp.aiter_bytes(65536):
                        self._jiffies += len(chunk)
                        if decoder.stdin and not decoder.stdin.is_closing():
                            decoder.stdin.write(chunk)
                            await decoder.stdin.drain()
            if decoder.stdin and not decoder.stdin.is_closing():
                decoder.stdin.close()
            await read_task
            await self._send_stat(b"STMd")
        except asyncio.CancelledError:
            if decoder and decoder.returncode is None:
                decoder.kill()
            raise
        except Exception:
            logger.exception("Audio fetch error from %s", url)
            await self._send_stat(b"STMn")

    async def _send_stat(self, event: bytes) -> None:
        ts = int(time.monotonic() * 1000) & 0xFFFFFFFF
        data = event
        data += struct.pack("B", 0)  # crlf
        data += struct.pack("B", 0)  # mas_initialized
        data += struct.pack("B", 0)  # mas_mode
        data += struct.pack("!L", 0)  # rptr
        data += struct.pack("!L", 0)  # wptr
        data += struct.pack("!Q", 0)  # bytes_received
        data += struct.pack("!H", 0)  # signal_strength
        data += struct.pack("!L", ts)  # jiffies
        data += struct.pack("!L", 0)  # output_buffer_size
        data += struct.pack("!L", 0)  # output_buffer_fullness
        data += struct.pack("!L", 0)  # elapsed_seconds
        data += struct.pack("!H", 0)  # voltage
        data += struct.pack("!L", 0)  # elapsed_milliseconds
        data += struct.pack("!L", ts)  # server_timestamp
        data += struct.pack("!H", 0)  # error_code
        await self._send_frame(b"STAT", data)

    async def _heartbeat_loop(self) -> None:
        try:
            while self._connected:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                await self._send_stat(b"STMt")
        except (asyncio.CancelledError, ConnectionResetError, OSError):
            pass
