from __future__ import annotations

import pytest

from rose_cinema.services.seed_pool import SeedPool
from rose_cinema.services.track_picker import TrackPicker

from tests._fakes import FakeLLM, mk_track, mk_artist


class FakeSeedBuilder:
    """Mimics SeedPoolBuilder.build for tests without invoking the real one."""

    def __init__(self, pool: SeedPool):
        self._pool = pool
        self.calls = 0

    async def build(self, music_source: str, target_count: int, **kwargs) -> SeedPool:
        self.calls += 1
        return self._pool


def _pool(*tracks):
    return SeedPool(
        seed_label="seed",
        seed_artist=mk_artist("1", "Seed"),
        tracks=list(tracks),
        artists=[],
        source="artist_seed",
    )


@pytest.mark.asyncio
async def test_pool_path_uses_indices():
    pool = _pool(
        mk_track("T0", "T0", "A"),
        mk_track("T1", "T1", "B"),
        mk_track("T2", "T2", "C"),
        mk_track("T3", "T3", "D"),
        mk_track("T4", "T4", "E"),
        mk_track("T5", "T5", "F"),
    )
    llm = FakeLLM(['{"picks": [0, 2, 5]}'])
    picker = TrackPicker(llm, catalog=None, seed_builder=FakeSeedBuilder(pool))

    songs = await picker.pick(music_source="seed", target_minutes=10, avg_song_secs=200)
    assert [s.apple_music_id for s in songs] == ["T0", "T2", "T5"]
    assert [s.artist for s in songs] == ["A", "C", "F"]


@pytest.mark.asyncio
async def test_pool_path_drops_invalid_indices():
    pool = _pool(
        mk_track("T0", "T0", "A"),
        mk_track("T1", "T1", "B"),
    )
    llm = FakeLLM(['{"picks": [0, 99, -1, 1, "x"]}'])
    picker = TrackPicker(llm, catalog=None, seed_builder=FakeSeedBuilder(pool))

    songs = await picker.pick(music_source="seed", target_minutes=4, avg_song_secs=200)
    assert [s.apple_music_id for s in songs] == ["T0", "T1"]


@pytest.mark.asyncio
async def test_pool_path_caps_then_tops_up():
    """LLM piles up artist A → cap drops to 2 → top-up fills to target from pool."""
    pool = _pool(
        mk_track("X1", "X1", "A"),
        mk_track("X2", "X2", "A"),
        mk_track("X3", "X3", "A"),
        mk_track("X4", "X4", "A"),
        mk_track("Y1", "Y1", "B"),
    )
    llm = FakeLLM(['{"picks": [0, 1, 2, 3]}'])
    picker = TrackPicker(llm, catalog=None, seed_builder=FakeSeedBuilder(pool))

    # target_count = max(3, round(8*60/200)) = 3
    songs = await picker.pick(music_source="seed", target_minutes=8, avg_song_secs=200)
    assert sum(1 for s in songs if s.artist == "A") == 2
    assert sum(1 for s in songs if s.artist == "B") == 1   # top-up
    assert len(songs) == 3


@pytest.mark.asyncio
async def test_pool_path_topup_respects_cap():
    """When even the pool can't supply more variety, settle for what the cap allows."""
    pool = _pool(
        mk_track("X1", "X1", "A"),
        mk_track("X2", "X2", "A"),
        mk_track("X3", "X3", "A"),
        mk_track("Y1", "Y1", "B"),
        mk_track("Y2", "Y2", "B"),
    )
    llm = FakeLLM(['{"picks": [0, 1, 2, 3, 4]}'])
    picker = TrackPicker(llm, catalog=None, seed_builder=FakeSeedBuilder(pool))

    # target_count = round(20*60/200) = 6 — beyond what the pool's 2-cap can supply
    songs = await picker.pick(music_source="seed", target_minutes=20, avg_song_secs=200)
    # LLM picked 5 tracks, after cap that's 2A + 2B = 4. Top-up has nothing to add (all artists capped).
    assert len(songs) == 4
    assert sum(1 for s in songs if s.artist == "A") == 2
    assert sum(1 for s in songs if s.artist == "B") == 2


@pytest.mark.asyncio
async def test_falls_back_to_legacy_when_pool_empty():
    """Empty pool → legacy LLM-discovery path (single LLM call returning JSON array)."""
    empty_pool = SeedPool(
        seed_label="seed", seed_artist=None,
        tracks=[], artists=[], source="none",
    )
    llm = FakeLLM(['[{"title": "Foo", "artist": "Bar", "year": "2020", "duration_secs": 200}]'])
    picker = TrackPicker(llm, catalog=None, seed_builder=FakeSeedBuilder(empty_pool))

    songs = await picker.pick(music_source="anything", target_minutes=8, avg_song_secs=200)
    assert len(songs) == 1
    assert songs[0].title == "Foo"
    assert songs[0].artist == "Bar"


@pytest.mark.asyncio
async def test_pool_path_handles_unparseable_llm_output():
    """If the LLM curation reply is unparsable, fall back to taking the first N pool tracks."""
    pool = _pool(
        mk_track("T0", "T0", "A"),
        mk_track("T1", "T1", "B"),
        mk_track("T2", "T2", "C"),
    )
    llm = FakeLLM(["not json at all, sorry"])
    picker = TrackPicker(llm, catalog=None, seed_builder=FakeSeedBuilder(pool))

    songs = await picker.pick(music_source="seed", target_minutes=6, avg_song_secs=200)
    # target_count = round(6*60/200) = 2; cap_per_artist allows ≤2 per artist.
    assert len(songs) >= 1
    assert all(s.apple_music_id in {"T0", "T1", "T2"} for s in songs)


@pytest.mark.asyncio
async def test_pool_listing_includes_genre_tags():
    """When pool.artist_tags is populated, the LLM prompt includes {tag, tag} per track."""
    pool = SeedPool(
        seed_label="seed",
        seed_artist=mk_artist("1", "Seed"),
        tracks=[mk_track("T0", "Song A", "Alice"), mk_track("T1", "Song B", "Bob")],
        artists=[mk_artist("A1", "Alice", "Pop"), mk_artist("B1", "Bob")],
        source="artist_seed",
        artist_tags={
            "alice": ("dream pop", "shoegaze"),
            "bob": ("grunge", "punk rock"),
        },
    )
    llm = FakeLLM(['{"picks": [0, 1]}'])
    picker = TrackPicker(llm, catalog=None, seed_builder=FakeSeedBuilder(pool))

    await picker.pick(music_source="seed", target_minutes=6, avg_song_secs=200)

    prompt = llm.calls[0][-1].content
    assert "{dream pop, shoegaze}" in prompt
    assert "{grunge, punk rock}" in prompt
