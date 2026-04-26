from __future__ import annotations

import logging
from pathlib import Path

from openai import AsyncOpenAI

from rose_cinema.providers import TTSProvider

logger = logging.getLogger(__name__)


class OpenAITTS(TTSProvider):
    def __init__(self, api_key: str):
        self._client = AsyncOpenAI(api_key=api_key)

    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
    ) -> Path:
        output_path = Path(output_path).with_suffix(".mp3")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        response = await self._client.audio.speech.create(
            model="tts-1",
            voice=voice_id,
            input=text,
            response_format="mp3",
        )
        output_path.write_bytes(response.content)
        return output_path

    def list_voices(self) -> list[dict]:
        return [
            {"id": "alloy", "name": "Alloy"},
            {"id": "echo", "name": "Echo"},
            {"id": "fable", "name": "Fable"},
            {"id": "onyx", "name": "Onyx"},
            {"id": "nova", "name": "Nova"},
            {"id": "shimmer", "name": "Shimmer"},
        ]
