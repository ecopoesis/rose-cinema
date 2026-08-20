from __future__ import annotations

import pytest

from rose_cinema.services.catalog import CatalogArtistViews
from rose_cinema.services.seed_pool import SeedPoolBuilder

from tests._fakes import FakeCatalog, FakeLLM, FakeMusicBrainz, mk_artist, mk_track


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


@pytest.mark.asyncio
async def test_multi_artist_pool_from_explicit_sources():
    bi = mk_artist("1", "Bon Iver", "Indie Folk")
    iw = mk_artist("2", "Iron & Wine", "Folk")
    catalog = FakeCatalog(
        artists_by_query={
            "bon iver": [bi],
            "iron & wine": [iw],
        },
        artist_views={
            "1": CatalogArtistViews(
                artist=bi,
                top_songs=[mk_track(f"B{i}", f"BI Song {i}", "Bon Iver") for i in range(6)],
                similar_artists=[],
            ),
            "2": CatalogArtistViews(
                artist=iw,
                top_songs=[mk_track(f"I{i}", f"IW Song {i}", "Iron & Wine") for i in range(6)],
                similar_artists=[],
            ),
        },
    )
    builder = SeedPoolBuilder(catalog, FakeLLM([]))
    pool = await builder.build(
        "chill folk", target_count=8,
        source_artists=["Bon Iver", "Iron & Wine"],
    )
    assert pool.source == "multi_artist_seed"
    artists_in_pool = {t.artist for t in pool.tracks}
    assert "Bon Iver" in artists_in_pool
    assert "Iron & Wine" in artists_in_pool


@pytest.mark.asyncio
async def test_pre_parse_extracts_artists_from_descriptive_source():
    bi = mk_artist("1", "Bon Iver", "Indie Folk")
    iw = mk_artist("2", "Iron & Wine", "Folk")
    catalog = FakeCatalog(
        artists_by_query={
            "bon iver": [bi],
            "iron & wine": [iw],
        },
        artist_views={
            "1": CatalogArtistViews(
                artist=bi,
                top_songs=[mk_track(f"B{i}", f"BI Song {i}", "Bon Iver") for i in range(6)],
                similar_artists=[],
            ),
            "2": CatalogArtistViews(
                artist=iw,
                top_songs=[mk_track(f"I{i}", f"IW Song {i}", "Iron & Wine") for i in range(6)],
                similar_artists=[],
            ),
        },
    )
    llm = FakeLLM([
        '{"artists":["Bon Iver","Iron & Wine"],"albums":[],"tracks":[],'
        '"style":"chill indie folk","constraints":"nothing too upbeat"}',
    ])
    builder = SeedPoolBuilder(catalog, llm)
    pool = await builder.build(
        "chill indie folk like Bon Iver and Iron & Wine, nothing too upbeat",
        target_count=8,
    )
    assert pool.source == "multi_artist_seed"
    assert pool.style_hint == "chill indie folk"
    assert pool.constraints == "nothing too upbeat"
    artists_in_pool = {t.artist for t in pool.tracks}
    assert "Bon Iver" in artists_in_pool
    assert "Iron & Wine" in artists_in_pool


@pytest.mark.asyncio
async def test_explicit_tracks_added_to_pool():
    seed = mk_artist("1", "Seed", "Rock")
    explicit_track = mk_track("EX1", "Skinny Love", "Bon Iver")
    catalog = FakeCatalog(
        artists_by_query={"seed": [seed]},
        artist_views={"1": CatalogArtistViews(
            artist=seed,
            top_songs=[mk_track("S1", "Seed Song", "Seed")],
            similar_artists=[],
        )},
        songs_by_query={"skinny love": [explicit_track]},
    )
    builder = SeedPoolBuilder(catalog, FakeLLM([]))
    pool = await builder.build(
        "Seed", target_count=5,
        source_tracks=["Skinny Love"],
    )
    ids = [t.apple_music_id for t in pool.tracks]
    assert "EX1" in ids


@pytest.mark.asyncio
async def test_enrich_merges_am_and_mb_tags():
    seed = mk_artist("1", "Palace", "Alternative", "Rock")
    catalog = FakeCatalog(
        artists_by_query={"palace": [seed]},
        artist_views={"1": CatalogArtistViews(
            artist=seed,
            top_songs=[mk_track("X1", "X", "Palace")],
            similar_artists=[],
        )},
    )
    mb = FakeMusicBrainz(tags_by_name={"palace": ("shoegaze", "dream pop", "alternative")})
    builder = SeedPoolBuilder(catalog, FakeLLM([]), mb_client=mb)
    pool = await builder.build("Palace", target_count=5)

    tags = pool.artist_tags.get("palace", ())
    assert "shoegaze" in tags
    assert "dream pop" in tags
    assert "Rock" in tags
    assert tags.index("shoegaze") < tags.index("Rock")


@pytest.mark.asyncio
async def test_enrich_skipped_when_no_mb_client():
    seed = mk_artist("1", "Palace", "Alternative", "Rock")
    catalog = FakeCatalog(
        artists_by_query={"palace": [seed]},
        artist_views={"1": CatalogArtistViews(
            artist=seed,
            top_songs=[mk_track("X1", "X", "Palace")],
            similar_artists=[],
        )},
    )
    builder = SeedPoolBuilder(catalog, FakeLLM([]))
    pool = await builder.build("Palace", target_count=5)

    tags = pool.artist_tags.get("palace", ())
    assert "Alternative" in tags
    assert "Rock" in tags


# ── Variety sliders ────────────────────────────────────────────────────


def _views(artist, tracks, similar=()):
    return CatalogArtistViews(artist=artist, top_songs=tracks, similar_artists=list(similar))


@pytest.mark.asyncio
async def test_discovery_rate_forwarded_on_uncached_path():
    mcr = mk_artist("1", "My Chemical Romance", "Alternative")
    catalog = FakeCatalog(
        artists_by_query={"my chemical romance": [mcr]},
        artist_views={"1": _views(mcr, [mk_track("100", "Helena", mcr.name)])},
    )
    builder = SeedPoolBuilder(catalog, FakeLLM([]))

    await builder.build("My Chemical Romance", target_count=10, discovery_rate=1.0)
    assert ("get_artist_views", "1", 10, 12) in catalog.calls

    catalog.calls.clear()
    await builder.build("My Chemical Romance", target_count=10, discovery_rate=0.25)
    assert ("get_artist_views", "1", 10, 3) in catalog.calls


@pytest.mark.asyncio
async def test_genre_gate_drops_nonoverlapping_similar_at_zero_variety():
    seed = mk_artist("1", "Seed Artist", "Alternative")
    kin = mk_artist("2", "Kin Artist", "Alternative")
    stranger = mk_artist("3", "Stranger", "Country")
    catalog = FakeCatalog(
        artists_by_query={"seed artist": [seed]},
        artist_views={
            "1": _views(seed, [mk_track("100", "S", seed.name)], [kin, stranger]),
            "2": _views(kin, [mk_track("200", "K", kin.name)]),
            "3": _views(stranger, [mk_track("300", "C", stranger.name)]),
        },
    )
    builder = SeedPoolBuilder(catalog, FakeLLM([]))

    pool = await builder.build("Seed Artist", target_count=10, genre_variety=0.0)
    names = {a.name for a in pool.artists}
    assert "Kin Artist" in names
    assert "Stranger" not in names

    pool = await builder.build("Seed Artist", target_count=10, genre_variety=1.0)
    assert "Stranger" in {a.name for a in pool.artists}


@pytest.mark.asyncio
async def test_year_window_trims_far_tracks_when_pool_is_rich():
    seed = mk_artist("1", "Seed Artist")
    similars = [mk_artist(str(10 + i), f"Similar {i}") for i in range(6)]
    old_timer = mk_artist("99", "Old Timer")
    views = {
        "1": _views(
            seed,
            [mk_track(f"1{i}", f"S{i}", seed.name, "2000") for i in range(10)],
            similars + [old_timer],
        ),
        "99": _views(old_timer, [mk_track(f"99{i}", f"O{i}", old_timer.name, "1970") for i in range(4)]),
    }
    for i, a in enumerate(similars):
        views[a.apple_music_id] = _views(
            a, [mk_track(f"{a.apple_music_id}{j}", f"T{i}{j}", a.name, "2001") for j in range(12)],
        )
    catalog = FakeCatalog(artists_by_query={"seed artist": [seed]}, artist_views=views)
    builder = SeedPoolBuilder(catalog, FakeLLM([]))

    pool = await builder.build(
        "Seed Artist", target_count=3, discovery_rate=1.0, year_variety=0.0,
    )
    assert len(pool.tracks) >= 24
    assert all(t.year != "1970" for t in pool.tracks)


@pytest.mark.asyncio
async def test_year_window_respects_pool_floor():
    seed = mk_artist("1", "Seed Artist")
    old_timer = mk_artist("99", "Old Timer")
    catalog = FakeCatalog(
        artists_by_query={"seed artist": [seed]},
        artist_views={
            "1": _views(
                seed,
                [mk_track(f"1{i}", f"S{i}", seed.name, "2000") for i in range(10)],
                [old_timer],
            ),
            "99": _views(
                old_timer,
                [mk_track(f"99{i}", f"O{i}", old_timer.name, "1970") for i in range(4)],
            ),
        },
    )
    builder = SeedPoolBuilder(catalog, FakeLLM([]))

    pool = await builder.build(
        "Seed Artist", target_count=3, discovery_rate=1.0, year_variety=0.0,
    )
    # Pool is below the 24-track floor, so nothing may be dropped.
    assert any(t.year == "1970" for t in pool.tracks)


@pytest.mark.asyncio
async def test_multi_artist_genre_threshold_scales_with_variety():
    rock_a = mk_artist("1", "Rock A", "Rock")
    rock_b = mk_artist("2", "Rock B", "Rock")
    jazzer = mk_artist("9", "Jazzer", "Jazz")
    views = {
        "1": _views(rock_a, [mk_track("10", "RA", rock_a.name)], [jazzer]),
        "2": _views(rock_b, [mk_track("20", "RB", rock_b.name)]),
        "9": _views(jazzer, [mk_track("90", "J", jazzer.name)]),
    }
    catalog = FakeCatalog(
        artists_by_query={"rock a": [rock_a], "rock b": [rock_b]},
        artist_views=views,
    )
    builder = SeedPoolBuilder(catalog, FakeLLM([]))

    pool = await builder.build(
        "two rock bands", target_count=10,
        source_artists=["Rock A", "Rock B"], genre_variety=0.0,
    )
    assert "Jazzer" not in {a.name for a in pool.artists}

    pool = await builder.build(
        "two rock bands", target_count=10,
        source_artists=["Rock A", "Rock B"], genre_variety=1.0,
    )
    assert "Jazzer" in {a.name for a in pool.artists}
