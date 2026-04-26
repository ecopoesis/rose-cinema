from __future__ import annotations

import logging
from pathlib import Path

import httpx

from rose_cinema.providers import TTSProvider

logger = logging.getLogger(__name__)

ELEVENLABS_API = "https://api.elevenlabs.io/v1"


class ElevenLabsTTS(TTSProvider):
    def __init__(self, api_key: str):
        self._api_key = api_key

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
    ) -> Path:
        output_path = Path(output_path).with_suffix(".mp3")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{ELEVENLABS_API}/text-to-speech/{voice_id}",
                headers={"xi-api-key": self._api_key},
                json={
                    "text": text,
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                    },
                },
            )
            resp.raise_for_status()
            output_path.write_bytes(resp.content)

        return output_path

    def list_voices(self) -> list[dict]:
        # Sync helper — called rarely, fine to block
        import httpx as httpx_sync

        resp = httpx_sync.get(
            f"{ELEVENLABS_API}/voices",
            headers={"xi-api-key": self._api_key},
        )
        resp.raise_for_status()
        voices = resp.json().get("voices", [])
        return [
            {
                "id": v["voice_id"],
                "name": v["name"],
                "preview_url": v.get("preview_url", ""),
            }
            for v in voices
        ]
