from __future__ import annotations

import asyncio
import difflib
import logging
import re
import subprocess
from pathlib import Path

import httpx

from rose_cinema.config import settings
from rose_cinema.providers import TTSProvider

logger = logging.getLogger(__name__)

_APPROVED_TAGS = frozenset({
    "laugh", "chuckle", "sigh", "gasp", "cough",
    "clear throat", "sniff", "groan", "shush",
})

_EXPRESSION_TAG_RE = re.compile(r"\[([^\]]*)\]")


def _normalize_expression_tags(text: str) -> str:
    """Keep approved tags, fuzzy-match near misses, replace junk with a pause."""
    def _replace(m: re.Match) -> str:
        tag = m.group(1).strip().lower()
        if tag in _APPROVED_TAGS:
            return f"[{tag}]"
        matches = difflib.get_close_matches(tag, list(_APPROVED_TAGS), n=1, cutoff=0.6)
        if matches:
            logger.debug("Fuzzy-matched expression tag [%s] → [%s]", m.group(1), matches[0])
            return f"[{matches[0]}]"
        logger.debug("Dropping unknown expression tag [%s] → pause", m.group(1))
        return "..."

    cleaned = _EXPRESSION_TAG_RE.sub(_replace, text)
    return re.sub(r"  +", " ", cleaned).strip()

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(1)
    return _semaphore


class ChatterboxTTS(TTSProvider):
    """Chatterbox TTS via the devnen/Chatterbox-TTS-Server HTTP API."""

    def __init__(self, base_url: str | None = None):
        self._base_url = (base_url or settings.chatterbox_url).rstrip("/")

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
        reference_audio: str = "",
    ) -> Path:
        output_path = Path(output_path)
        wav_path = output_path.with_suffix(".wav")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        text = _normalize_expression_tags(text)

        if reference_audio:
            payload = {
                "text": text,
                "voice_mode": "clone",
                "reference_audio_filename": reference_audio,
            }
        else:
            payload = {
                "text": text,
                "voice_mode": "predefined",
                "predefined_voice_id": voice_id,
            }

        payload["output_format"] = "wav"

        async with _get_semaphore():
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(f"{self._base_url}/tts", json=payload)
                resp.raise_for_status()
                wav_path.write_bytes(resp.content)

        mp3_path = output_path.with_suffix(".mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav_path),
             "-ar", "44100", "-ac", "2", "-q:a", "2", str(mp3_path)],
            capture_output=True,
            timeout=60,
        )
        wav_path.unlink(missing_ok=True)
        return mp3_path

    def list_voices(self) -> list[dict]:
        try:
            resp = httpx.get(
                f"{self._base_url}/v1/audio/voices",
                timeout=10.0,
            )
            resp.raise_for_status()
            names = resp.json().get("voices", [])
            return [{"id": v, "name": v.removesuffix(".wav")} for v in names]
        except Exception:
            logger.warning("Could not fetch Chatterbox voices, returning defaults")
            return [{"id": "Emily.wav", "name": "Emily"}]
