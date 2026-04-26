from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from rose_cinema.config import settings
from rose_cinema.providers import TTSProvider

logger = logging.getLogger(__name__)


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
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        wav_path = output_path.with_suffix(".wav")

        try:
            proc = subprocess.run(
                [
                    "piper",
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
                ["ffmpeg", "-y", "-i", str(wav_path), "-q:a", "2", str(mp3_path)],
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
            {"id": "en_US-lessac-medium", "name": "Lessac (US English, Medium)"},
            {"id": "en_US-lessac-high", "name": "Lessac (US English, High)"},
            {"id": "en_US-libritts_r-medium", "name": "LibriTTS-R (US English, Medium)"},
            {"id": "en_US-amy-medium", "name": "Amy (US English, Medium)"},
            {"id": "en_GB-alba-medium", "name": "Alba (British English, Medium)"},
        ]
