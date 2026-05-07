from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

from rose_cinema.config import settings
from rose_cinema.providers import TTSProvider

logger = logging.getLogger(__name__)


def _piper_bin() -> str:
    sibling = Path(sys.executable).parent / "piper"
    if sibling.exists():
        return str(sibling)
    return shutil.which("piper") or "piper"


class PiperTTS(TTSProvider):
    """
    Local TTS using Piper (https://github.com/rhasspy/piper).
    Runs as a subprocess — no server needed.
    Models are downloaded on first use to ~/.local/share/piper_models.
    """

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
        reference_audio: str = "",
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        wav_path = output_path.with_suffix(".wav")

        try:
            proc = subprocess.run(
                [
                    _piper_bin(),
                    "--model", voice_id,
                    "--data-dir", settings.piper_data_dir,
                    "--output_file", str(wav_path),
                ],
                input=text,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode != 0:
                logger.error("Piper failed: %s", proc.stderr)
                raise RuntimeError(f"Piper TTS failed: {proc.stderr}")

            # Convert WAV → MP3 via ffmpeg for smaller files
            mp3_path = output_path.with_suffix(".mp3")
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav_path),
                 "-ar", "44100", "-ac", "2", "-q:a", "2", str(mp3_path)],
                capture_output=True,
                timeout=60,
            )
            wav_path.unlink(missing_ok=True)
            return mp3_path

        except FileNotFoundError:
            logger.error("Piper binary not found — is piper-tts installed?")
            raise

    def list_voices(self) -> list[dict]:
        return [
            # Bryce Beattie's audiobook-narrator voices (brycebeattie.com/files/tts)
            {"id": "cori-high",   "name": "Cori (warm female, high quality)"},
            {"id": "kristin",     "name": "Kristin (female)"},
            {"id": "bryce",       "name": "Bryce (male)"},
            {"id": "norman",      "name": "Norman (deep male)"},
            {"id": "mv2",         "name": "ManyVoice (multi-speaker)"},
            {"id": "jenny",       "name": "Jenny (female)"},
            # Default Piper voice (auto-downloaded by piper-tts)
            {"id": "en_US-lessac-medium", "name": "Lessac (US English, fallback)"},
        ]
