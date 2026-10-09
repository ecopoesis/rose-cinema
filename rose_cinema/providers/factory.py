from __future__ import annotations

from rose_cinema.config import settings
from rose_cinema.providers import LLMProvider, TTSProvider
from rose_cinema.providers.llm_openai_compat import OpenAICompatibleLLM
from rose_cinema.providers.tts_piper import PiperTTS
from rose_cinema.providers.tts_elevenlabs import ElevenLabsTTS
from rose_cinema.providers.tts_openai import OpenAITTS
from rose_cinema.providers.tts_chatterbox import ChatterboxTTS


def get_llm_provider() -> LLMProvider:
    """Resolve the configured LLM provider.

    "anthropic" uses the native Messages API; anything else is treated as an
    OpenAI-compatible endpoint (Ollama, OpenAI, OpenRouter, ...).
    """
    if settings.llm_provider == "anthropic":
        if not settings.llm_model.startswith("claude-"):
            raise RuntimeError(
                f"LLM_PROVIDER=anthropic but LLM_MODEL={settings.llm_model!r} is not a "
                "Claude model: set LLM_MODEL to a Claude model (e.g. claude-sonnet-5-5), "
                "or set LLM_PROVIDER=ollama"
            )
        from rose_cinema.providers.llm_anthropic import AnthropicLLM, resolve_anthropic_api_key

        return AnthropicLLM(
            api_key=resolve_anthropic_api_key(),
            model=settings.llm_model,
            effort=settings.llm_effort,
        )
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
