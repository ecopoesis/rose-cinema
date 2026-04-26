"""Tests for Rose Cinema core logic."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from rose_cinema.repositories import DJRecord, StationRecord
from rose_cinema.services.dj_script import DJScriptService, build_system_prompt


# ── DJ Script Service ──────────────────────────────────────────────────


class TestBuildSystemPrompt:
    def test_low_babble_rate_is_brief(self):
        dj = DJRecord(name="Test DJ", agent_md="Be cool.")
        prompt = build_system_prompt(dj, babble_rate=0.1, max_seconds=30)
        assert "extremely brief" in prompt.lower()
        assert "Be cool." in prompt

    def test_high_babble_rate_is_entertaining(self):
        dj = DJRecord(name="Test DJ", agent_md="")
        prompt = build_system_prompt(dj, babble_rate=0.9, max_seconds=60)
        assert "entertainer" in prompt.lower()

    def test_agent_md_included(self):
        dj = DJRecord(name="Test DJ", agent_md="You love jazz and hate mornings.")
        prompt = build_system_prompt(dj, babble_rate=0.5, max_seconds=30)
        assert "You love jazz and hate mornings." in prompt


class TestDJScriptService:
    @pytest.mark.asyncio
    async def test_generate_transition(self):
        mock_llm = AsyncMock()
        mock_llm.complete.return_value = "That was a great song! Up next..."

        service = DJScriptService(mock_llm)
        dj = DJRecord(name="Velvet", agent_md="Be smooth.")

        result = await service.generate_transition(
            dj=dj,
            previous_song={"title": "Song A", "artist": "Artist A"},
            next_song={"title": "Song B", "artist": "Artist B"},
            babble_rate=0.5,
            max_seconds=30,
        )

        assert result == "That was a great song! Up next..."
        mock_llm.complete.assert_called_once()

    def test_should_talk_respects_rate(self):
        mock_llm = AsyncMock()
        service = DJScriptService(mock_llm)

        # Rate 0 should never talk
        results = [service.should_talk(0.0) for _ in range(100)]
        assert not any(results)

        # Rate 1 should always talk
        results = [service.should_talk(1.0) for _ in range(100)]
        assert all(results)


# ── Station Builder (unit-level) ───────────────────────────────────────


class TestSongMetadata:
    def test_to_dict(self):
        from rose_cinema.services.station_builder import SongMetadata

        song = SongMetadata(title="Test", artist="Artist", album="Album", year="2024")
        d = song.to_dict()
        assert d["title"] == "Test"
        assert d["artist"] == "Artist"


class TestPlaylistEntry:
    def test_to_dict(self):
        from rose_cinema.services.station_builder import PlaylistEntry

        entry = PlaylistEntry(type="song", title="Foo", artist="Bar")
        d = entry.to_dict()
        assert d["type"] == "song"
        assert d["title"] == "Foo"
