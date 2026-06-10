from __future__ import annotations

import shutil
import subprocess

import pytest

from rose_cinema.config import settings
from rose_cinema.services.ezstream_manager import EzstreamSession

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def _make_fixture(path, codec_args):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         *codec_args, str(path)],
        check=True,
    )


@pytest.mark.asyncio
async def test_mixed_mp3_and_m4a_entries_stream_as_mp3(tmp_path, monkeypatch):
    """DJ .mp3 + cached .m4a tracks must yield a valid all-MP3 stream.

    Regression: the concat demuxer requires uniform codecs, so .m4a tracks
    get a one-time MP3 conversion before entering the concat list.
    """
    monkeypatch.setattr(settings, "streams_dir", str(tmp_path / "streams"))
    monkeypatch.setattr(settings, "stream_mp3_dir", str(tmp_path / "stream_mp3"))

    dj_mp3 = tmp_path / "dj.mp3"
    song_m4a = tmp_path / "song.m4a"
    _make_fixture(dj_mp3, ["-c:a", "libmp3lame", "-b:a", "128k"])
    _make_fixture(song_m4a, ["-c:a", "aac", "-b:a", "128k"])

    entries = [
        {"type": "dj", "audio_file": str(dj_mp3), "duration_secs": 2},
        {"type": "song", "apple_music_id": "A1", "artist": "X", "title": "Y",
         "duration_secs": 2},
    ]
    session = EzstreamSession(
        station_id="st", uid="u", run_id="r",
        entries=entries, entry_index=0, station_name="Test FM",
        cached_paths={"A1": song_m4a},
    )
    await session.start()
    try:
        concat = (tmp_path / "streams" / "st_u" / "concat.txt").read_text()
        assert ".m4a" not in concat
        assert concat.count(".mp3") == 2

        buf = b""
        gen = session.stream_plain()
        async for chunk in gen:
            buf += chunk
            if len(buf) >= 4096:
                break
        await gen.aclose()
        assert buf[:3] == b"ID3" or (buf[0] == 0xFF and (buf[1] & 0xE0) == 0xE0)
    finally:
        await session.stop()
    assert not session.is_running
