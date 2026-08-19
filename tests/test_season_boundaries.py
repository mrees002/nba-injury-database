"""Tests for season boundary fallback classification."""

from datetime import date

from app.nba.season_boundaries import SEASON_BOUNDARIES, classify_by_season_boundary


def test_2026_27_entry_exists_but_is_empty():
    assert "2026-27" in SEASON_BOUNDARIES
    assert SEASON_BOUNDARIES["2026-27"] == {}


def test_2026_27_dates_return_none():
    # No phase boundaries configured yet, so all 2026-27 dates are uncovered.
    samples = [
        date(2026, 10, 1),   # hypothetical preseason
        date(2026, 11, 15),  # hypothetical regular season
        date(2027, 4, 20),   # hypothetical play-in
        date(2027, 6, 1),    # hypothetical playoffs
    ]
    for d in samples:
        assert classify_by_season_boundary(d) == (None, None), f"date={d}"


def test_uncovered_offseason_dates_return_none():
    # July gap that falls between 2025-26 playoffs end and any future season.
    assert classify_by_season_boundary(date(2026, 7, 15)) == (None, None)


def test_2025_26_regular_season_unchanged():
    assert classify_by_season_boundary(date(2026, 1, 15)) == ("2025-26", "regular")


def test_2025_26_preseason_unchanged():
    assert classify_by_season_boundary(date(2025, 10, 5)) == ("2025-26", "preseason")


def test_2025_26_play_in_unchanged():
    assert classify_by_season_boundary(date(2026, 4, 15)) == ("2025-26", "play_in")


def test_2025_26_playoffs_unchanged():
    assert classify_by_season_boundary(date(2026, 5, 10)) == ("2025-26", "playoffs")


def test_2024_25_regular_season_unchanged():
    assert classify_by_season_boundary(date(2025, 1, 20)) == ("2024-25", "regular")


def test_2019_20_covid_bubble_unchanged():
    assert classify_by_season_boundary(date(2020, 10, 11)) == ("2019-20", "playoffs")


def test_2020_21_delayed_start_unchanged():
    assert classify_by_season_boundary(date(2020, 12, 15)) == ("2020-21", "preseason")


def test_boundary_inclusive_start():
    # First day of 2024-25 regular season
    assert classify_by_season_boundary(date(2024, 10, 22)) == ("2024-25", "regular")


def test_boundary_inclusive_end():
    # Last day of 2024-25 regular season
    assert classify_by_season_boundary(date(2025, 4, 13)) == ("2024-25", "regular")


def test_date_before_all_seasons():
    assert classify_by_season_boundary(date(2019, 1, 1)) == (None, None)


def test_partial_config_does_not_crash():
    """An entry with some but not all phases should work without error."""
    import copy

    modified = copy.deepcopy(SEASON_BOUNDARIES)
    modified["2026-27"] = {"regular": type(SEASON_BOUNDARIES["2025-26"]["regular"])(
        start=date(2026, 10, 20), end=date(2027, 4, 11),
    )}
    # pylint: disable=protected-access
    import app.nba.season_boundaries as mod

    original = mod.SEASON_BOUNDARIES
    try:
        mod.SEASON_BOUNDARIES = modified
        assert classify_by_season_boundary(date(2026, 12, 1)) == ("2026-27", "regular")
        assert classify_by_season_boundary(date(2026, 10, 1)) == (None, None)
    finally:
        mod.SEASON_BOUNDARIES = original
