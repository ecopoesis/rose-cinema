from __future__ import annotations

import logging

import httpx
from openai import AsyncOpenAI

from rose_cinema.providers import LLMMessage, LLMProvider

logger = logging.getLogger(__name__)


class OpenAICompatibleLLM(LLMProvider):
    """
    Works with any OpenAI-compatible endpoint:
      - Ollama:    base_url=http://ollama:11434/v1, api_key=not-needed
      - Anthropic: base_url=https://api.anthropic.com/v1/, api_key=sk-ant-...
      - OpenAI:    base_url=https://api.openai.com/v1, api_key=sk-...

    Ollama endpoints are auto-detected and use the native /api/chat with
    thinking disabled, avoiding reasoning-token overhead on the OpenAI shim.
    """

    def __init__(self, base_url: str, api_key: str, model: str):
        self._model = model
        self._timeout = httpx.Timeout(timeout=1800.0, connect=30.0)

        ollama_base = self._detect_ollama(base_url)
        if ollama_base:
            self._ollama_base = ollama_base
            self._client = None
            logger.info("Using Ollama native API at %s", ollama_base)
        else:
            self._ollama_base = None
            self._client = AsyncOpenAI(
                base_url=base_url, api_key=api_key,
                timeout=self._timeout,
            )

    @staticmethod
    def _detect_ollama(base_url: str) -> str | None:
        base = base_url.rstrip("/")
        if ":11434" in base:
            return base.removesuffix("/v1").removesuffix("/v1/")
        return None

    async def complete(
        self,
        messages: list[LLMMessage],
        temperature: float = 0.8,
        max_tokens: int = 500,
    ) -> str:
        if self._ollama_base:
            return await self._complete_ollama(messages, temperature, max_tokens)
        return await self._complete_openai(messages, temperature, max_tokens)

    async def _complete_ollama(
        self,
        messages: list[LLMMessage],
        temperature: float,
        max_tokens: int,
    ) -> str:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._ollama_base}/api/chat", json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("message", {}).get("content", "") or ""
        except Exception:
            logger.exception("Ollama completion failed")
            raise

    async def _complete_openai(
        self,
        messages: list[LLMMessage],
        temperature: float,
        max_tokens: int,
    ) -> str:
        oai_messages = [{"role": m.role, "content": m.content} for m in messages]
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=oai_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception:
            logger.exception("LLM completion failed")
            raise
