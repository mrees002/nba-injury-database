from __future__ import annotations

from datetime import date


def get_official_nba_season(value: date) -> str:
    """Return the NBA season, including the 2020 restart's exceptional October finish."""

    if date(2020, 10, 1) <= value <= date(2020, 10, 11):
        return "2019-20"
    year = value.year
    if value.month >= 10:
        return f"{year}-{str(year + 1)[-2:]}"
    return f"{year - 1}-{str(year)[-2:]}"
