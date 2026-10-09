"""AnthropicLLM, usage tracking, key resolution and provider selection — no network."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from rose_cinema.config import settings
from rose_cinema.providers import LLMMessage, LLMProvider, LLMRefusalError
from rose_cinema.providers import factory
from rose_cinema.providers.llm_anthropic import AnthropicLLM, resolve_anthropic_api_key
from rose_cinema.providers.llm_openai_compat import OpenAICompatibleLLM
from rose_cinema.providers.usage import record_llm_usage, track_llm_usage
from rose_cinema.services.queue import QueueWorker


def _response(
    content: list | None = None,
    stop_reason: str = "end_turn",
    stop_details=None,
    usage=None,
):
    return SimpleNamespace(
        content=content
        if content is not None
        else [SimpleNamespace(type="text", text="hello")],
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=usage
        or SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
        ),
    )


class _FakeClient:
    def __init__(self, response):
        self.calls: list[dict] = []
        self._response = response
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def _llm(response=None, effort: str = "low") -> tuple[AnthropicLLM, _FakeClient]:
    llm = AnthropicLLM(api_key="test-key", model="claude-sonnet-5-5", effort=effort)
    client = _FakeClient(response or _response())
    llm._client = client
    return llm, client


MESSAGES = [
    LLMMessage(role="system", content="You are a DJ."),
    LLMMessage(role="system", content="Be brief."),
    LLMMessage(role="user", content="Intro please."),
]


class TestAnthropicRequest:
    async def test_system_messages_become_top_level_system(self):
        llm, client = _llm()
        await llm.complete(MESSAGES)
        call = client.calls[0]
        assert call["system"] == "You are a DJ.\n\nBe brief."
        assert call["messages"] == [{"role": "user", "content": "Intro please."}]
        assert call["model"] == "claude-sonnet-5-5"

    async def test_no_system_key_without_system_messages(self):
        llm, client = _llm()
        await llm.complete([LLMMessage(role="user", content="hi")])
        assert "system" not in client.calls[0]

    async def test_no_sampling_or_thinking_params(self):
        llm, client = _llm()
        await llm.complete(MESSAGES, temperature=0.3)
        call = client.calls[0]
        for forbidden in ("temperature", "top_p", "top_k", "thinking", "tool_choice"):
            assert forbidden not in call

    async def test_effort_and_fallback_opt_in(self):
        llm, client = _llm(effort="medium")
        await llm.complete(MESSAGES)
        call = client.calls[0]
        assert call["output_config"] == {"effort": "medium"}
        assert call["betas"] == ["server-side-fallback-2026-07-01"]
        assert call["fallbacks"] == "default"

    async def test_max_tokens_floor(self):
        llm, client = _llm()
        await llm.complete(MESSAGES, max_tokens=1500)
        await llm.complete(MESSAGES, max_tokens=32000)
        assert client.calls[0]["max_tokens"] == 16000
        assert client.calls[1]["max_tokens"] == 32000


class TestAnthropicResponse:
    async def test_text_extracted_and_thinking_skipped(self):
        llm, _ = _llm(_response(content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text="Good "),
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text="evening."),
        ]))
        assert await llm.complete(MESSAGES) == "Good evening."

    async def test_refusal_raises_with_category(self):
        llm, _ = _llm(_response(
            content=[], stop_reason="refusal",
            stop_details=SimpleNamespace(category="cyber"),
        ))
        with pytest.raises(LLMRefusalError, match="cyber"):
            await llm.complete(MESSAGES)

    async def test_refusal_without_details_raises(self):
        llm, _ = _llm(_response(content=[], stop_reason="refusal"))
        with pytest.raises(LLMRefusalError, match="refused"):
            await llm.complete(MESSAGES)

    async def test_max_tokens_still_returns_text(self):
        llm, _ = _llm(_response(stop_reason="max_tokens"))
        assert await llm.complete(MESSAGES) == "hello"

    async def test_api_error_propagates(self):
        llm, client = _llm()

        async def boom(**kwargs):
            raise ValueError("boom")

        client.beta.messages.create = boom
        with pytest.raises(ValueError, match="boom"):
            await llm.complete(MESSAGES)


class TestUsageTracking:
    async def test_usage_recorded_into_active_tracker(self):
        llm, _ = _llm(_response(usage=SimpleNamespace(
            input_tokens=10, output_tokens=7,
            cache_creation_input_tokens=3, cache_read_input_tokens=100,
        )))
        with track_llm_usage() as usage:
            await llm.complete(MESSAGES)
            await llm.complete(MESSAGES)
        assert (usage.input_tokens, usage.output_tokens) == (226, 14)

    async def test_refusal_still_records_usage(self):
        llm, _ = _llm(_response(content=[], stop_reason="refusal"))
        with track_llm_usage() as usage:
            with pytest.raises(RuntimeError):
                await llm.complete(MESSAGES)
        assert (usage.input_tokens, usage.output_tokens) == (10, 5)

    def test_record_without_tracker_is_noop(self):
        record_llm_usage(5, 5)
        with track_llm_usage() as usage:
            pass
        assert (usage.input_tokens, usage.output_tokens) == (0, 0)

    async def test_gathered_child_tasks_share_the_accumulator(self):
        async def call() -> None:
            await asyncio.sleep(0)
            record_llm_usage(2, 1)

        with track_llm_usage() as usage:
            await asyncio.gather(call(), call(), call())
        assert (usage.input_tokens, usage.output_tokens) == (6, 3)

    async def test_trackers_do_not_leak_across_blocks(self):
        with track_llm_usage() as first:
            record_llm_usage(1, 1)
        with track_llm_usage() as second:
            record_llm_usage(2, 2)
        record_llm_usage(9, 9)
        assert (first.input_tokens, second.input_tokens) == (1, 2)

    async def test_openai_compat_records_usage(self):
        llm = OpenAICompatibleLLM("https://example.invalid/v1", "k", "m")

        async def create(**kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=4),
            )

        llm._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        with track_llm_usage() as usage:
            assert await llm.complete(MESSAGES) == "ok"
        assert (usage.input_tokens, usage.output_tokens) == (11, 4)

    async def test_openai_compat_tolerates_missing_usage(self):
        llm = OpenAICompatibleLLM("https://example.invalid/v1", "k", "m")

        async def create(**kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=None,
            )

        llm._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        with track_llm_usage() as usage:
            assert await llm.complete(MESSAGES) == "ok"
        assert (usage.input_tokens, usage.output_tokens) == (0, 0)


class TestKeyResolution:
    def test_env_key_wins_over_file(self, monkeypatch, tmp_path):
        key_file = tmp_path / "key"
        key_file.write_text("from-file\n")
        monkeypatch.setattr(settings, "anthropic_api_key", "from-env")
        monkeypatch.setattr(settings, "anthropic_api_key_file", str(key_file))
        assert resolve_anthropic_api_key() == "from-env"

    def test_file_used_when_env_empty(self, monkeypatch, tmp_path):
        key_file = tmp_path / "key"
        key_file.write_text("  from-file\n")
        monkeypatch.setattr(settings, "anthropic_api_key", "")
        monkeypatch.setattr(settings, "anthropic_api_key_file", str(key_file))
        assert resolve_anthropic_api_key() == "from-file"

    def test_error_names_both_options(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "anthropic_api_key", "")
        monkeypatch.setattr(settings, "anthropic_api_key_file", str(tmp_path / "missing"))
        with pytest.raises(RuntimeError) as exc:
            resolve_anthropic_api_key()
        assert "ANTHROPIC_API_KEY" in str(exc.value)
        assert "ANTHROPIC_API_KEY_FILE" in str(exc.value)

    def test_directory_at_key_path_is_no_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "anthropic_api_key", "")
        monkeypatch.setattr(settings, "anthropic_api_key_file", str(tmp_path))
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY_FILE"):
            resolve_anthropic_api_key()

    def test_empty_key_file_is_no_key(self, monkeypatch, tmp_path):
        key_file = tmp_path / "key"
        key_file.write_text("  \n")
        monkeypatch.setattr(settings, "anthropic_api_key", "")
        monkeypatch.setattr(settings, "anthropic_api_key_file", str(key_file))
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY_FILE"):
            resolve_anthropic_api_key()


    def test_env_key_trailing_newline_is_stripped(self, monkeypatch):
        monkeypatch.setattr(settings, "anthropic_api_key", "from-env\n")
        monkeypatch.setattr(settings, "anthropic_api_key_file", "")
        assert resolve_anthropic_api_key() == "from-env"

    def test_multiline_key_file_is_malformed_and_not_echoed(self, monkeypatch, tmp_path):
        key_file = tmp_path / "key"
        key_file.write_text("SECRETVALUE1\nSECRETVALUE2\n")
        monkeypatch.setattr(settings, "anthropic_api_key", "")
        monkeypatch.setattr(settings, "anthropic_api_key_file", str(key_file))
        with pytest.raises(RuntimeError) as exc:
            resolve_anthropic_api_key()
        message = str(exc.value)
        assert "malformed" in message and str(key_file) in message
        assert "SECRETVALUE" not in message

    @pytest.mark.parametrize("value", ["SECRET VALUE", "SECRET\tVALUE", "SECRET\x00VALUE"])
    def test_malformed_env_key_is_rejected_and_not_echoed(self, monkeypatch, value):
        monkeypatch.setattr(settings, "anthropic_api_key", value)
        monkeypatch.setattr(settings, "anthropic_api_key_file", "")
        with pytest.raises(RuntimeError) as exc:
            resolve_anthropic_api_key()
        message = str(exc.value)
        assert "malformed" in message and "ANTHROPIC_API_KEY" in message
        assert "SECRET" not in message and "VALUE" not in message


class TestFactory:
    def test_anthropic_with_non_claude_model_is_rejected(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "anthropic")
        monkeypatch.setattr(settings, "llm_model", "qwen3.6:27b")
        monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
        with pytest.raises(RuntimeError) as exc:
            factory.get_llm_provider()
        assert "LLM_MODEL" in str(exc.value) and "LLM_PROVIDER=ollama" in str(exc.value)

    def test_claude_model_allowed_on_openai_compat_path(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "openrouter")
        monkeypatch.setattr(settings, "llm_base_url", "https://example.invalid/v1")
        monkeypatch.setattr(settings, "llm_model", "claude-sonnet-5-5")
        assert isinstance(factory.get_llm_provider(), OpenAICompatibleLLM)

    def test_effort_typo_fails_validation(self):
        from pydantic import ValidationError
        from rose_cinema.config import Settings

        with pytest.raises(ValidationError):
            Settings(llm_effort="hihg")

    def test_anthropic_selected(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "anthropic")
        monkeypatch.setattr(settings, "llm_model", "claude-sonnet-5-5")
        monkeypatch.setattr(settings, "llm_effort", "medium")
        monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
        llm = factory.get_llm_provider()
        assert isinstance(llm, AnthropicLLM)
        assert (llm._model, llm._effort) == ("claude-sonnet-5-5", "medium")

    def test_anthropic_without_key_fails_at_construction_not_import(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "anthropic")
        monkeypatch.setattr(settings, "llm_model", "claude-sonnet-5-5")
        monkeypatch.setattr(settings, "anthropic_api_key", "")
        monkeypatch.setattr(settings, "anthropic_api_key_file", "")
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            factory.get_llm_provider()

    def test_other_providers_use_openai_compat(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "ollama")
        monkeypatch.setattr(settings, "llm_base_url", "http://localhost:11434/v1")
        monkeypatch.setattr(settings, "anthropic_api_key", "")
        monkeypatch.setattr(settings, "anthropic_api_key_file", "")
        assert isinstance(factory.get_llm_provider(), OpenAICompatibleLLM)


class _StubSession:
    def __init__(self, log: list, events: dict, fail_usage: bool):
        self._log = log
        self._events = events
        self._fail_usage = fail_usage

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @asynccontextmanager
    async def begin_nested(self):
        yield

    async def execute(self, stmt):
        if self._fail_usage:
            raise RuntimeError("lock timeout")
        self._log.append(stmt)

    async def get(self, model, key):
        return self._events.setdefault(key, SimpleNamespace(
            id=key, run_id="run-1", step_type="noop", step_index=0,
            retry_count=0, status="processing", result=None, payload={},
            error_message=None, started_at=None, completed_at=None,
        ))

    async def flush(self):
        pass

    async def commit(self):
        self._log.append("commit")


class TestQueueUsageIncrement:
    """Stubbed session only — the suite has no database fixture."""

    async def _run(self, monkeypatch, handler, fail_usage: bool = False):
        from rose_cinema.services import step_handlers

        log: list = []
        events: dict = {}
        dispatched: list = []
        monkeypatch.setitem(step_handlers.HANDLERS, "noop", handler)
        worker = QueueWorker(lambda: _StubSession(log, events, fail_usage))

        async def dispatch(session, completed):
            dispatched.append(completed.id)

        monkeypatch.setattr(worker, "_dispatch_next", dispatch)
        await worker._process(SimpleNamespace(
            id="ev-1", run_id="run-1", step_type="noop", payload={},
        ))
        return log, events.get("ev-1"), dispatched

    @staticmethod
    def _increments(log: list) -> dict[str, int]:
        """Column name -> amount added, read from the UPDATE's SET clause."""
        (stmt,) = [entry for entry in log if entry != "commit"]
        assert stmt.table.name == "playlist_runs"
        out = {}
        for column, expr in stmt._values.items():
            assert expr.left.name == column.name
            out[column.name] = expr.right.value
        assert stmt.compile().params["id_1"] == "run-1"
        return out

    async def test_successful_handler_increments_run(self, monkeypatch):
        async def handler(payload: dict) -> dict:
            record_llm_usage(120, 30)
            return {"ok": True}

        log, ev, dispatched = await self._run(monkeypatch, handler)
        assert self._increments(log) == {"llm_input_tokens": 120, "llm_output_tokens": 30}
        assert ev.status == "completed" and dispatched == ["ev-1"]
        assert log[-1] == "commit" and log.count("commit") == 1

    async def test_failed_handler_still_increments_run(self, monkeypatch):
        async def handler(payload: dict) -> dict:
            record_llm_usage(50, 4)
            raise RuntimeError("boom")

        log, ev, dispatched = await self._run(monkeypatch, handler)
        assert self._increments(log) == {"llm_input_tokens": 50, "llm_output_tokens": 4}
        assert ev.status == "pending" and ev.retry_count == 1
        assert dispatched == [] and log.count("commit") == 1

    async def test_no_usage_means_no_update(self, monkeypatch):
        async def handler(payload: dict) -> dict:
            return {}

        log, ev, _ = await self._run(monkeypatch, handler)
        assert log == ["commit"] and ev.status == "completed"

    async def test_usage_update_failure_does_not_block_completion(self, monkeypatch):
        async def handler(payload: dict) -> dict:
            record_llm_usage(120, 30)
            return {"ok": True}

        log, ev, dispatched = await self._run(monkeypatch, handler, fail_usage=True)
        assert ev.status == "completed" and ev.result == {"ok": True}
        assert dispatched == ["ev-1"] and log == ["commit"]

    async def test_usage_update_failure_does_not_block_failure(self, monkeypatch):
        async def handler(payload: dict) -> dict:
            record_llm_usage(50, 4)
            raise RuntimeError("boom")

        log, ev, dispatched = await self._run(monkeypatch, handler, fail_usage=True)
        assert ev.status == "pending" and ev.error_message == "boom"
        assert dispatched == [] and log == ["commit"]


class _RefusingLLM(LLMProvider):
    async def complete(self, messages, temperature=0.8, max_tokens=500) -> str:
        raise LLMRefusalError("Anthropic refused the request (category: general_harms)")


class TestRefusedSteps:
    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch):
        from rose_cinema.repositories import DJRecord
        from rose_cinema.services import step_handlers

        async def load_dj(dj_id: str) -> DJRecord:
            return DJRecord(id=dj_id, name="Velvet", agent_md="Be smooth.")

        monkeypatch.setattr(step_handlers, "_load_dj", load_dj)
        monkeypatch.setattr(step_handlers, "get_llm_provider", lambda: _RefusingLLM())
        self.handlers = step_handlers

    async def test_refused_intro_returns_empty_script(self):
        result = await self.handlers.handle_generate_intro_script({
            "dj_id": "dj-1", "station_name": "S", "babble_rate": 0.5, "max_secs": 30,
        })
        assert result == {"script": ""}

    async def test_refused_transition_returns_empty_script(self):
        result = await self.handlers.handle_generate_transition({
            "dj_id": "dj-1", "next_song": {"title": "B", "artist": "b"},
            "babble_rate": 0.5, "max_secs": 30, "song_index": 3,
        })
        assert result == {"script": "", "song_index": 3}

    async def test_refused_pick_tracks_still_fails(self, monkeypatch):
        from rose_cinema.services import musickit

        monkeypatch.setattr(musickit, "get_music_catalog", lambda: None)
        with pytest.raises(LLMRefusalError):
            await self.handlers.handle_pick_tracks({
                "station_id": "station-1", "music_source": "jazz", "length_minutes": 30,
            })
