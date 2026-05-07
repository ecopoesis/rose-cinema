from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from pathlib import Path
from textwrap import dedent

from rose_cinema.config import settings

logger = logging.getLogger(__name__)


class EzstreamSession:
    def __init__(
        self,
        station_id: str,
        uid: str,
        run_id: str,
        entries: list[dict],
        entry_index: int,
        station_name: str,
        mount: str,
    ) -> None:
        self.station_id = station_id
        self.uid = uid
        self.run_id = run_id
        self.entries = entries
        self.entry_index = entry_index
        self.station_name = station_name
        self.mount = mount
        self._proc: asyncio.subprocess.Process | None = None
        self._work_dir: Path | None = None

    @property
    def current_entry(self) -> dict:
        idx = self._read_counter() + self.entry_index
        if 0 <= idx < len(self.entries):
            return self.entries[idx]
        return {}

    def _read_counter(self) -> int:
        if self._work_dir:
            try:
                return int((self._work_dir / "meta_counter").read_text().strip())
            except Exception:
                pass
        return 0

    def _build_playlist(self, work_dir: Path) -> Path:
        m3u = work_dir / "playlist.m3u"
        tracks_dir = Path(settings.tracks_dir)
        lines: list[str] = []

        for entry in self.entries[self.entry_index:]:
            if entry.get("type") == "dj":
                audio_file = entry.get("audio_file", "")
                if audio_file and Path(audio_file).exists():
                    lines.append(str(Path(audio_file).resolve()))
            elif entry.get("type") == "song":
                aid = entry.get("apple_music_id", "")
                cached = tracks_dir / f"{aid}.mp3"
                if cached.exists():
                    lines.append(str(cached.resolve()))
                else:
                    logger.warning("Track not cached, skipping: %s", aid)

        m3u.write_text("\n".join(lines) + "\n")
        return m3u

    def _build_metadata_script(self, work_dir: Path) -> Path:
        script = work_dir / "metadata.py"
        index_file = work_dir / "track_index.json"
        counter_file = work_dir / "meta_counter"
        index_data: list[dict] = []
        tracks_dir = Path(settings.tracks_dir)
        for entry in self.entries[self.entry_index:]:
            if entry.get("type") == "dj":
                audio_file = entry.get("audio_file", "")
                if audio_file and Path(audio_file).exists():
                    index_data.append({
                        "title": self.station_name,
                        "artist": "",
                    })
            elif entry.get("type") == "song":
                aid = entry.get("apple_music_id", "")
                cached = tracks_dir / f"{aid}.mp3"
                if cached.exists():
                    index_data.append({
                        "title": entry.get("title", ""),
                        "artist": entry.get("artist", ""),
                    })

        index_file.write_text(json.dumps(index_data))
        counter_file.write_text("-1")

        script.write_text(dedent(f"""\
            #!/usr/bin/env python3
            import json, sys

            INDEX = "{index_file}"
            COUNTER = "{counter_file}"
            arg = sys.argv[1] if len(sys.argv) > 1 else ""

            try:
                pos = int(open(COUNTER).read().strip())
            except Exception:
                pos = -1

            if not arg:
                pos += 1
                open(COUNTER, "w").write(str(pos))

            try:
                tracks = json.load(open(INDEX))
                t = tracks[pos] if 0 <= pos < len(tracks) else {{}}
            except Exception:
                t = {{}}

            a, ti = t.get("artist", ""), t.get("title", "")
            if arg == "artist":
                print(a)
            elif arg == "title":
                print(ti)
            else:
                print(f"{{a}} - {{ti}}" if a else (ti or "Unknown"))
        """))
        os.chmod(str(script), 0o755)
        return script

    def _build_config(self, work_dir: Path, m3u: Path, metadata_script: Path) -> Path:
        cfg = work_dir / "ezstream.xml"
        bitrate = settings.native_stream_bitrate.replace("k", "")
        cfg.write_text(dedent(f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <ezstream>
              <servers>
                <server>
                  <name>default</name>
                  <hostname>{settings.icecast_host}</hostname>
                  <port>{settings.icecast_port}</port>
                  <password>{settings.icecast_source_password}</password>
                </server>
              </servers>
              <streams>
                <stream>
                  <name>default</name>
                  <mountpoint>{self.mount}</mountpoint>
                  <server>default</server>
                  <intake>default</intake>
                  <format>MP3</format>
                  <stream_name>{self.station_name}</stream_name>
                  <stream_bitrate>{bitrate}</stream_bitrate>
                  <stream_samplerate>44100</stream_samplerate>
                  <stream_channels>2</stream_channels>
                  <public>0</public>
                </stream>
              </streams>
              <intakes>
                <intake>
                  <name>default</name>
                  <type>playlist</type>
                  <filename>{m3u}</filename>
                  <shuffle>0</shuffle>
                  <stream_once>1</stream_once>
                </intake>
              </intakes>
              <metadata>
                <program>{metadata_script}</program>
              </metadata>
            </ezstream>
        """))
        return cfg

    async def start(self) -> None:
        work_dir = Path(settings.streams_dir) / f"{self.station_id}_{self.uid}"
        work_dir.mkdir(parents=True, exist_ok=True)
        self._work_dir = work_dir

        m3u = self._build_playlist(work_dir)
        meta_script = self._build_metadata_script(work_dir)
        cfg = self._build_config(work_dir, m3u, meta_script)

        self._proc = await asyncio.create_subprocess_exec(
            "ezstream", "-c", str(cfg), "-v",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info(
            "Started ezstream pid=%d mount=%s for %s:%s",
            self._proc.pid, self.mount, self.station_id[:8], self.uid,
        )

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
            logger.info("Stopped ezstream pid=%d", self._proc.pid)

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def skip_track(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.send_signal(signal.SIGUSR1)


class EzstreamManager:
    def __init__(self) -> None:
        self._sessions: dict[str, EzstreamSession] = {}

    def _key(self, station_id: str, uid: str) -> str:
        return f"{station_id}:{uid}"

    async def start_session(
        self,
        station_id: str,
        uid: str,
        run_id: str,
        entries: list[dict],
        entry_index: int,
        station_name: str,
    ) -> EzstreamSession:
        key = self._key(station_id, uid)

        existing = self._sessions.get(key)
        if existing and existing.is_running:
            await existing.stop()

        mount = f"/live-{station_id[:8]}-{uid[:16]}"
        session = EzstreamSession(
            station_id=station_id,
            uid=uid,
            run_id=run_id,
            entries=entries,
            entry_index=entry_index,
            station_name=station_name,
            mount=mount,
        )
        await session.start()
        self._sessions[key] = session
        return session

    async def stop_session(self, station_id: str, uid: str) -> None:
        key = self._key(station_id, uid)
        session = self._sessions.pop(key, None)
        if session:
            await session.stop()

    def get_session(self, station_id: str, uid: str) -> EzstreamSession | None:
        key = self._key(station_id, uid)
        return self._sessions.get(key)

    async def stop_all(self) -> None:
        for session in list(self._sessions.values()):
            await session.stop()
        self._sessions.clear()
