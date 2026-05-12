from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1

logger = logging.getLogger(__name__)


@dataclass
class PlaylistEntry:
    """One item in the generated playlist."""
    type: str  # "song" | "dj"
    title: str = ""
    artist: str = ""
    album: str = ""
    year: str = ""
    apple_music_id: str = ""
    audio_file: str = ""  # path to DJ audio clip (only for type="dj")
    script: str = ""  # DJ text (only for type="dj")
    duration_secs: float = 0.0

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "year": self.year,
            "apple_music_id": self.apple_music_id,
            "audio_file": self.audio_file,
            "script": self.script,
            "duration_secs": self.duration_secs,
        }


@dataclass
class SongMetadata:
    title: str
    artist: str
    album: str = ""
    year: str = ""
    apple_music_id: str = ""
    duration_secs: float = 210.0  # default 3.5 min
    track_number: int = 0

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "year": self.year,
        }


def _tag_dj_audio(
    path: Path, title: str, artist: str,
) -> None:
    """Write title/artist ID3 tags. No APIC — embedded art causes MA's
    builtin provider to shadow the real image URL we set via update."""
    if not path.exists() or path.suffix.lower() != ".mp3":
        return
    try:
        audio = MP3(path, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        audio.tags.add(TIT2(encoding=3, text=[title]))
        audio.tags.add(TPE1(encoding=3, text=[artist]))
        audio.save()
    except Exception:
        logger.warning("Failed to tag %s, continuing without metadata", path)


