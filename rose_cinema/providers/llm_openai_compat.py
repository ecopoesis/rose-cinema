from __future__ import annotations

import logging

from openai import AsyncOpenAI

from rose_cinema.providers import LLMMessage, LLMProvider

logger = logging.getLogger(__name__)


class OpenAICompatibleLLM(LLMProvider):
    """
    Works with any OpenAI-compatible endpoint:
      - Ollama:    base_url=http://ollama:11434/v1, api_key=not-needed
      - Anthropic: base_url=https://api.anthropic.com/v1/, api_key=sk-ant-...
      - OpenAI:    base_url=https://api.openai.com/v1, api_key=sk-...
    """

    def __init__(self, base_url: str, api_key: str, model: str):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self._model = model

    async def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.8,
        max_tokens: int = 500,
    ) -> str:
        oai_messages = [{"role": m.role, "content": m.content} for m in messages]

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=oai_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body={"think": False},
            )
            return response.choices[0].message.content or ""
        except Exception:
            logger.exception("LLM completion failed")
            raise
