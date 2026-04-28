from __future__ import annotations

import pytest

from rose_cinema.services.catalog import CatalogArtistViews
from rose_cinema.services.seed_pool import SeedPoolBuilder

from tests._fakes import FakeCatalog, FakeLLM, mk_artist, mk_track


@pytest.mark.asyncio
async def test_artist_seed_resolution_and_pool():
    mcr = mk_artist("1", "My Chemical Romance", "Alternative")
    fob = mk_artist("2", "Fall Out Boy", "Alternative")
    panic = mk_artist("3", "Panic! At the Disco", "Alternative")
    catalog = FakeCatalog(
        artists_by_query={"my chemical romance": [mcr]},
        artist_views={
            "1": CatalogArtistViews(
                artist=mcr,
                top_songs=[mk_track(f"100{i}", f"MCR Song {i}", mcr.name) for i in range(10)],
                similar_artists=[fob, panic],
            ),
            "2": CatalogArtistViews(
                artist=fob,
                top_songs=[mk_track(f"200{i}", f"FOB Song {i}", fob.name) for i in range(8)],
                similar_artists=[],
            ),
            "3": CatalogArtistViews(
                artist=panic,
                top_songs=[mk_track(f"300{i}", f"Panic Song {i}", panic.name) for i in range(8)],
                similar_artists=[],
            ),
        },
    )
    builder = SeedPoolBuilder(catalog, FakeLLM([]))

    pool = await builder.build("My Chemical Romance", target_count=10)

    assert pool.source == "artist_seed"
    assert pool.seed_artist is not None and pool.seed_artist.name == "My Chemical Romance"
    assert len(pool.tracks) > 0
    assert {a.name for a in pool.artists} >= {"My Chemical Romance", "Fall Out Boy", "Panic! At the Disco"}


@pytest.mark.asyncio
async def test_track_seed_falls_through_to_artist():
    mcr = mk_artist("1", "My Chemical Romance", "Alternative")
    seed_song = mk_track("S1", "Welcome to the Black Parade", "My Chemical Romance")
    catalog = FakeCatalog(
        artists_by_query={
            "welcome to the black parade by my chemical romance": [],
            "my chemical romance": [mcr],
        },
        songs_by_query={"welcome to the black parade by my chemical romance": [seed_song]},
        artist_views={
            "1": CatalogArtistViews(
                artist=mcr,
                top_songs=[mk_track("100", "Helena", "My Chemical Romance")],
                similar_artists=[],
            ),
        },
    )
    builder = SeedPoolBuilder(catalog, FakeLLM([]))

    pool = await builder.build("Welcome to the Black Parade by My Chemical Romance", target_count=10)
    assert pool.source == "artist_seed"
    assert pool.seed_artist is not None and pool.seed_artist.name == "My Chemical Romance"


@pytest.mark.asyncio
async def test_theme_seed_uses_genre_charts():
    catalog = FakeCatalog(
        genres={"Alternative": "20", "Pop": "14"},
        genre_top_songs={
            "20": [
                mk_track("A1", "Alt Song 1", "Alt Artist", "2018"),
                mk_track("A2", "Alt Song 2", "Other Alt Artist", "2019"),
            ],
            "14": [mk_track("P1", "Pop Song 1", "Pop Artist", "2020")],
        },
    )
    llm = FakeLLM(['{"genres": ["Alternative", "Pop"]}'])
    builder = SeedPoolBuilder(catalog, llm)

    pool = await builder.build("rainy Tuesday afternoon", target_count=8)
    assert pool.source == "theme_seed"
    titles = [t.title for t in pool.tracks]
    assert "Alt Song 1" in titles
    assert "Pop Song 1" in titles


@pytest.mark.asyncio
async def test_dedup_and_per_artist_pool_cap():
    """The pool should de-dup by apple_music_id and cap each non-seed artist at 4."""
    seed = mk_artist("1", "Seed", "Rock")
    other = mk_artist("2", "Other", "Rock")
    catalog = FakeCatalog(
        artists_by_query={"seed": [seed]},
        artist_views={
            "1": CatalogArtistViews(
                artist=seed,
                # 8 distinct songs from seed; only first 5 should survive (seed cap = 5)
                top_songs=[mk_track(f"S{i}", f"Seed {i}", "Seed") for i in range(8)],
                similar_artists=[other],
            ),
            "2": CatalogArtistViews(
                artist=other,
                # 6 distinct + 2 dups; only first 4 unique should survive (default cap = 4)
                top_songs=(
                    [mk_track(f"O{i}", f"Other {i}", "Other") for i in range(6)]
                    + [mk_track("O0", "Other 0", "Other"), mk_track("O1", "Other 1", "Other")]
                ),
                similar_artists=[],
            ),
        },
    )
    builder = SeedPoolBuilder(catalog, FakeLLM([]))
    pool = await builder.build("Seed", target_count=10)

    seed_count = sum(1 for t in pool.tracks if t.artist == "Seed")
    other_count = sum(1 for t in pool.tracks if t.artist == "Other")
    assert seed_count <= 5, f"seed artist exceeded cap: {seed_count}"
    assert other_count <= 4, f"non-seed artist exceeded cap: {other_count}"
    # No duplicate apple_music_ids.
    ids = [t.apple_music_id for t in pool.tracks if t.apple_music_id]
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_total_miss_returns_empty():
    catalog = FakeCatalog(genres={"Rock": "21"})  # genres exist, but LLM picks nothing valid
    llm = FakeLLM(['{"genres": []}'])
    builder = SeedPoolBuilder(catalog, llm)
    pool = await builder.build("nonsense seed", target_count=10)
    assert pool.source == "none"
    assert pool.tracks == []


@pytest.mark.asyncio
async def test_no_cache_each_build_hits_catalog():
    seed = mk_artist("1", "Seed")
    catalog = FakeCatalog(
        artists_by_query={"seed": [seed]},
        artist_views={"1": CatalogArtistViews(
            artist=seed,
            top_songs=[mk_track("X1", "X", "Seed")],
            similar_artists=[],
        )},
    )
    builder = SeedPoolBuilder(catalog, FakeLLM([]))

    await builder.build("Seed", target_count=10)
    calls_before = len(catalog.calls)
    await builder.build("Seed", target_count=10)
    calls_after = len(catalog.calls)

    assert calls_after > calls_before, "each build should hit the catalog fresh"
