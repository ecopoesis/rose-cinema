from __future__ import annotations

import pytest

from rose_cinema.repositories import DJRecord
from rose_cinema.services.dj_script import (
    DJScriptService,
    build_system_prompt,
    strip_voice_ticks,
)

from tests._fakes import FakeLLM


def _dj(**kwargs) -> DJRecord:
    return DJRecord(id="d1", name="Velvet", tts_provider="chatterbox", **kwargs)


def test_ticks_off_by_default_omits_expression_block():
    prompt = build_system_prompt(_dj(), babble_rate=0.5, max_seconds=30)
    assert "VOICE EXPRESSION" not in prompt
    assert "no sound effects in brackets" in prompt


def test_ticks_on_includes_expression_block():
    prompt = build_system_prompt(_dj(voice_ticks=True), babble_rate=0.5, max_seconds=30)
    assert "VOICE EXPRESSION" in prompt
    assert "[laugh]" in prompt


def test_ticks_on_requires_chatterbox():
    dj = DJRecord(id="d1", name="V", tts_provider="piper", voice_ticks=True)
    prompt = build_system_prompt(dj, babble_rate=0.5, max_seconds=30)
    assert "VOICE EXPRESSION" not in prompt


def test_strip_voice_ticks_removes_bracket_tags_only():
    text = "That was great [laugh] wasn't it? Next up (from 1978) more."
    assert strip_voice_ticks(text) == "That was great wasn't it? Next up (from 1978) more."


@pytest.mark.asyncio
async def test_generated_script_stripped_when_ticks_off():
    llm = FakeLLM(["Welcome back [chuckle] to the show."])
    script = await DJScriptService(llm).generate_intro(
        dj=_dj(), station_name="The Palace", babble_rate=0.5, max_seconds=30,
    )
    assert script == "Welcome back to the show."


@pytest.mark.asyncio
async def test_generated_script_kept_when_ticks_on():
    llm = FakeLLM(["Welcome back [chuckle] to the show."])
    script = await DJScriptService(llm).generate_intro(
        dj=_dj(voice_ticks=True), station_name="The Palace",
        babble_rate=0.5, max_seconds=30,
    )
    assert "[chuckle]" in script
