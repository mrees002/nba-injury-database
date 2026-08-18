from datetime import date

from app.nba.seasons import get_official_nba_season, get_preseason_nba_season


def test_covid_restart_dates_remain_in_2019_20_season():
    assert get_official_nba_season(date(2020, 9, 30)) == "2019-20"
    assert get_official_nba_season(date(2020, 10, 1)) == "2019-20"
    assert get_official_nba_season(date(2020, 10, 11)) == "2019-20"
    assert get_official_nba_season(date(2020, 10, 12)) == "2020-21"


def test_normal_october_boundary_is_unchanged_for_other_years():
    assert get_official_nba_season(date(2025, 9, 30)) == "2024-25"
    assert get_official_nba_season(date(2025, 10, 1)) == "2025-26"


def test_preseason_september_belongs_to_upcoming_season():
    assert get_preseason_nba_season(date(2018, 9, 15)) == "2018-19"
    assert get_preseason_nba_season(date(2018, 9, 30)) == "2018-19"
    assert get_preseason_nba_season(date(2024, 9, 25)) == "2024-25"


def test_preseason_october_belongs_to_upcoming_season():
    assert get_preseason_nba_season(date(2018, 10, 1)) == "2018-19"
    assert get_preseason_nba_season(date(2018, 10, 15)) == "2018-19"
    assert get_preseason_nba_season(date(2024, 10, 8)) == "2024-25"


def test_preseason_august_belongs_to_current_season():
    assert get_preseason_nba_season(date(2018, 8, 30)) == "2017-18"
    assert get_preseason_nba_season(date(2024, 8, 15)) == "2023-24"


def test_preseason_does_not_break_regular_season_mapping():
    # Regular season mapping must stay unchanged
    assert get_official_nba_season(date(2018, 9, 15)) == "2017-18"
    assert get_official_nba_season(date(2018, 10, 1)) == "2018-19"
