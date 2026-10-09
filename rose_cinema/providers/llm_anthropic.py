from __future__ import annotations

import logging
from pathlib import Path

from anthropic import AsyncAnthropic

from rose_cinema.config import settings
from rose_cinema.providers import LLMMessage, LLMProvider, LLMRefusalError
from rose_cinema.providers.usage import record_llm_usage

logger = logging.getLogger(__name__)

# Adaptive thinking is always on and its tokens count against max_tokens,
# so the small budgets callers pass would truncate the visible answer.
MIN_MAX_TOKENS = 16000
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _checked_key(key: str, source: str) -> str:
    # Never echo the value: it is a secret even when malformed.
    if not key.isprintable() or any(c.isspace() for c in key):
        raise RuntimeError(
            f"Anthropic API key from {source} is malformed: "
            "it must be a single line containing only the key"
        )
    return key


def resolve_anthropic_api_key() -> str:
    key = settings.anthropic_api_key.strip()
    if key:
        return _checked_key(key, "ANTHROPIC_API_KEY")
    if settings.anthropic_api_key_file:
        path = Path(settings.anthropic_api_key_file)
        if path.is_file():
            key = path.read_text(encoding="utf-8").strip()
            if key:
                return _checked_key(key, f"ANTHROPIC_API_KEY_FILE ({path})")
    raise RuntimeError(
        "No Anthropic API key: set ANTHROPIC_API_KEY, or point "
        "ANTHROPIC_API_KEY_FILE at a file containing the key"
    )


class AnthropicLLM(LLMProvider):
    """Native Anthropic Messages API (official SDK)."""

    def __init__(self, api_key: str, model: str, effort: str = "low"):
        self._model = model
        self._effort = effort
        self._client = AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.8,
        max_tokens: int = 500,
    ) -> str:
        # temperature is accepted for interface parity only: current Claude
        # models reject non-default sampling parameters with a 400.
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        request: dict = {
            "model": self._model,
            "max_tokens": max(max_tokens, MIN_MAX_TOKENS),
            "messages": [
                {"role": m.role, "content": m.content}
                for m in messages
                if m.role != "system"
            ],
            "output_config": {"effort": self._effort},
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }
        if system:
            request["system"] = system

        try:
            response = await self._client.beta.messages.create(**request)
        except Exception:
            logger.exception("Anthropic completion failed")
            raise

        usage = response.usage
        record_llm_usage(
            usage.input_tokens
            + (usage.cache_creation_input_tokens or 0)
            + (usage.cache_read_input_tokens or 0),
            usage.output_tokens,
        )

        if response.stop_reason == "refusal":
            details = response.stop_details
            category = details.category if details is not None else None
            raise LLMRefusalError(
                f"Anthropic refused the request (category: {category or 'unspecified'})"
            )
        if response.stop_reason == "max_tokens":
            logger.warning(
                "Anthropic response hit max_tokens (%d); output may be truncated",
                request["max_tokens"],
            )

        return "".join(b.text for b in response.content if b.type == "text")
