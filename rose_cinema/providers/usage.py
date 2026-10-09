from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0


_current: ContextVar[LLMUsage | None] = ContextVar("llm_usage", default=None)


@contextmanager
def track_llm_usage() -> Iterator[LLMUsage]:
    usage = LLMUsage()
    token = _current.set(usage)
    try:
        yield usage
    finally:
        _current.reset(token)


def record_llm_usage(input_tokens: int, output_tokens: int) -> None:
    # Mutate in place: child tasks (asyncio.gather) get a copied context that
    # still points at this same object, so their usage reaches the tracker.
    usage = _current.get()
    if usage is None:
        return
    usage.input_tokens += input_tokens
    usage.output_tokens += output_tokens
