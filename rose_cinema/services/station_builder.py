from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1

from rose_cinema.providers import LLMProvider, TTSProvider
from rose_cinema.providers.factory import get_tts_provider
from rose_cinema.repositories import DJRecord, StationRecord, PlaylistRunRecord
from rose_cinema.services.dj_script import DJScriptService

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


class StationBuilder:
    """
    Orchestrates playlist generation:
    1. Takes a list of songs (provided by caller — Apple Music search is external)
    2. Decides where DJ segments go based on talk_rate
    3. Generates DJ scripts via LLM
    4. Synthesizes DJ audio via TTS
    5. Returns a structured playlist
    """

    def __init__(self, llm: LLMProvider, audio_dir: str = "data/dj_audio"):
        self._script_service = DJScriptService(llm)
        self._audio_dir = Path(audio_dir)

    async def build_playlist(
        self,
        station: StationRecord,
        dj: DJRecord,
        songs: list[SongMetadata],
    ) -> list[PlaylistEntry]:
        """Build a complete playlist with interleaved DJ segments."""
        tts = get_tts_provider(dj.tts_provider)
        run_id = str(uuid.uuid4())[:8]
        entries: list[PlaylistEntry] = []

        total_songs = len(songs)
        logger.info(
            "[%s] Starting playlist build: %d songs, DJ=%s (%s/%s), talk_rate=%.2f",
            station.name, total_songs, dj.name, dj.tts_provider, dj.tts_voice_id,
            station.dj_talk_rate,
        )

        # Optional station intro
        if station.dj_talk_rate > 0:
            logger.info("[%s] Generating station intro script…", station.name)
            intro_script = await self._script_service.generate_intro(
                dj=dj,
                station_name=station.name,
                babble_rate=station.dj_babble_rate,
                max_seconds=station.dj_max_length_secs,
            )
            logger.info("[%s] Synthesizing intro audio (%s)…", station.name, dj.tts_provider)
            intro_audio = await tts.synthesize(
                text=intro_script,
                voice_id=dj.tts_voice_id,
                output_path=self._audio_dir / f"{run_id}_intro",
                reference_audio=dj.tts_voice_ref,
            )
            _tag_dj_audio(intro_audio, f"{station.name} — Intro ({run_id})", dj.name)
            logger.info("[%s] Intro ready", station.name)
            entries.append(PlaylistEntry(
                type="dj",
                script=intro_script,
                audio_file=str(intro_audio),
            ))

        # Interleave songs and DJ segments
        previous_song: dict | None = None

        dj_seg = 0
        for i, song in enumerate(songs):
            # Check if the DJ should talk before this song
            if i > 0 and self._script_service.should_talk(station.dj_talk_rate):
                dj_seg += 1
                logger.info(
                    "[%s] DJ segment %d: scripting transition → \"%s\" by %s…",
                    station.name, dj_seg, song.title, song.artist,
                )
                script = await self._script_service.generate_transition(
                    dj=dj,
                    previous_song=previous_song,
                    next_song=song.to_dict(),
                    babble_rate=station.dj_babble_rate,
                    max_seconds=station.dj_max_length_secs,
                )
                logger.info("[%s] DJ segment %d: synthesizing audio…", station.name, dj_seg)
                audio_path = await tts.synthesize(
                    text=script,
                    voice_id=dj.tts_voice_id,
                    output_path=self._audio_dir / f"{run_id}_seg{i:03d}",
                    reference_audio=dj.tts_voice_ref,
                )
                _tag_dj_audio(audio_path, f"{station.name} — DJ {dj_seg} ({run_id})", dj.name)
                logger.info("[%s] DJ segment %d: done", station.name, dj_seg)
                entries.append(PlaylistEntry(
                    type="dj",
                    script=script,
                    audio_file=str(audio_path),
                ))

            logger.info(
                "[%s] Song %d/%d: \"%s\" by %s",
                station.name, i + 1, total_songs, song.title, song.artist,
            )
            # Add the song
            entries.append(PlaylistEntry(
                type="song",
                title=song.title,
                artist=song.artist,
                album=song.album,
                year=song.year,
                apple_music_id=song.apple_music_id,
                duration_secs=song.duration_secs,
            ))

            previous_song = song.to_dict()

        logger.info(
            "Built playlist for station '%s': %d entries (%d songs, %d DJ segments)",
            station.name,
            len(entries),
            sum(1 for e in entries if e.type == "song"),
            sum(1 for e in entries if e.type == "dj"),
        )
        return entries
