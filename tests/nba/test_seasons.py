from datetime import date

from app.nba.seasons import get_official_nba_season


def test_covid_restart_dates_remain_in_2019_20_season():
    assert get_official_nba_season(date(2020, 9, 30)) == "2019-20"
    assert get_official_nba_season(date(2020, 10, 1)) == "2019-20"
    assert get_official_nba_season(date(2020, 10, 11)) == "2019-20"
    assert get_official_nba_season(date(2020, 10, 12)) == "2020-21"


def test_normal_october_boundary_is_unchanged_for_other_years():
    assert get_official_nba_season(date(2025, 9, 30)) == "2024-25"
    assert get_official_nba_season(date(2025, 10, 1)) == "2025-26"
