from __future__ import annotations

import shutil
import subprocess

import pytest
from mutagen.mp4 import MP4, MP4Cover

from rose_cinema.config import settings
from rose_cinema.services.ezstream_manager import EzstreamSession

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def _ffmpeg(*args):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
    )


def _probe_duration(path) -> float:
    out = subprocess.run(
        ["ffprobe", "-hide_banner", "-loglevel", "error",
         "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


@pytest.mark.asyncio
async def test_stream_survives_dj_to_track_transition(tmp_path, monkeypatch):
    """The full playlist must stream as one valid MP3 across format boundaries.

    Mirrors production: DJ segments are 24kHz-mono .mp3, tracks are .m4a with
    embedded cover art. Regression for two concat-demuxer killers — a
    cover-art video stream appearing mid-concat, and sample-rate/channel
    changes between files.
    """
    monkeypatch.setattr(settings, "streams_dir", str(tmp_path / "streams"))
    monkeypatch.setattr(settings, "stream_mp3_dir", str(tmp_path / "stream_mp3"))

    dj_mp3 = tmp_path / "dj.mp3"
    song_m4a = tmp_path / "song.m4a"
    cover_png = tmp_path / "cover.png"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", str(dj_mp3))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=330:duration=2",
            "-ar", "44100", "-ac", "2", "-c:a", "aac", str(song_m4a))
    _ffmpeg("-f", "lavfi", "-i", "color=red:size=64x64", "-frames:v", "1", str(cover_png))
    audio = MP4(str(song_m4a))
    audio["covr"] = [MP4Cover(cover_png.read_bytes(), imageformat=MP4Cover.FORMAT_PNG)]
    audio.save()

    entries = [
        {"type": "dj", "audio_file": str(dj_mp3), "duration_secs": 1},
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
        assert concat.count(str(tmp_path / "stream_mp3")) == 2

        buf = b""
        gen = session.stream_plain()
        async for chunk in gen:  # drain to EOF — dies early if concat breaks
            buf += chunk
        await gen.aclose()

        assert buf[:3] == b"ID3" or (buf[0] == 0xFF and (buf[1] & 0xE0) == 0xE0)
        sample = tmp_path / "out.mp3"
        sample.write_bytes(buf)
        assert _probe_duration(sample) >= 2.5  # crossed the 1s DJ→track boundary
    finally:
        await session.stop()
    assert not session.is_running
