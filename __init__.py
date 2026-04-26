from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


class LLMProvider(ABC):
    """OpenAI-compatible chat completion provider."""

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.8,
        max_tokens: int = 500,
    ) -> str:
        """Return the assistant's text response."""
        ...


class TTSProvider(ABC):
    """Text-to-speech provider."""

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
    ) -> Path:
        """Render text to an audio file. Returns the path to the file."""
        ...

    @abstractmethod
    def list_voices(self) -> list[dict]:
        """Return available voices as [{"id": ..., "name": ..., "preview_url": ...}]."""
        ...
