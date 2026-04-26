from __future__ import annotations

from rose_cinema.config import settings
from rose_cinema.providers import LLMProvider, TTSProvider
from rose_cinema.providers.llm_openai_compat import OpenAICompatibleLLM
from rose_cinema.providers.tts_piper import PiperTTS
from rose_cinema.providers.tts_elevenlabs import ElevenLabsTTS
from rose_cinema.providers.tts_openai import OpenAITTS
from rose_cinema.providers.tts_chatterbox import ChatterboxTTS


def get_llm_provider() -> LLMProvider:
    """Resolve the configured LLM provider. All use OpenAI-compatible protocol."""
    return OpenAICompatibleLLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )


def get_tts_provider(provider_name: str | None = None, api_key: str | None = None) -> TTSProvider:
    """
    Resolve a TTS provider by name.
    Falls back to the global config if not specified.
    Each DJ can override with their own provider/voice.
    """
    name = provider_name or settings.tts_provider
    key = api_key or settings.tts_api_key

    match name:
        case "piper":
            return PiperTTS()
        case "elevenlabs":
            return ElevenLabsTTS(api_key=key)
        case "openai":
            return OpenAITTS(api_key=key)
        case "chatterbox":
            return ChatterboxTTS()
        case _:
            raise ValueError(f"Unknown TTS provider: {name}")
