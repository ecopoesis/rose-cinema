from __future__ import annotations

from rose_cinema.services.exclusions import (
    credited_artists,
    is_excluded,
    merge_exclusions,
    normalize_exclusions,
)


def test_plain_artist_match():
    excluded = normalize_exclusions(["Drake"])
    assert is_excluded("God's Plan", "Drake", excluded)
    assert is_excluded("God's Plan", "DRAKE", excluded)
    assert not is_excluded("Pink Moon", "Nick Drake", excluded)


def test_collab_artist_string():
    excluded = normalize_exclusions(["Drake"])
    assert is_excluded("Rich Flex", "Drake & 21 Savage", excluded)
    assert is_excluded("Sicko Mode", "Travis Scott, Drake", excluded)
    assert is_excluded("Mine", "Beyoncé Feat. Drake", excluded)
    assert is_excluded("What's Next", "Drake ft. Rick Ross", excluded)


def test_featured_credit_in_title():
    excluded = normalize_exclusions(["Drake"])
    assert is_excluded("Life Is Good (feat. Drake)", "Future", excluded)
    assert is_excluded("Work [feat. Drake]", "Rihanna", excluded)
    assert is_excluded("Yes Indeed (with Drake)", "Lil Baby", excluded)
    assert not is_excluded("Life Is Good (feat. Doja Cat)", "Future", excluded)


def test_band_names_with_separator_words_are_safe():
    excluded = normalize_exclusions(["Florence"])
    assert not is_excluded("Dog Days Are Over", "Florence + the Machine", excluded)
    excluded = normalize_exclusions(["Lil Nas"])
    assert not is_excluded("Old Town Road", "Lil Nas X", excluded)


def test_x_collab_split():
    excluded = normalize_exclusions(["KAYTRANADA"])
    assert is_excluded("Twin Flame", "KAYTRANADA x Anderson .Paak", excluded)


def test_full_multi_artist_name_still_matches():
    excluded = normalize_exclusions(["Tyler, The Creator"])
    assert is_excluded("EARFQUAKE", "Tyler, The Creator", excluded)


def test_credited_artists_extraction():
    names = credited_artists("Life Is Good (feat. Drake & Lil Baby)", "Future")
    assert names == {"future", "drake", "lil baby"}


def test_merge_exclusions_cumulative_and_deduped():
    merged = merge_exclusions(["Drake", "Kanye West"], ["drake", "Chris Brown"], None)
    assert merged == ["Drake", "Kanye West", "Chris Brown"]


def test_empty_exclusions_never_match():
    assert not is_excluded("Anything", "Anyone", set())
