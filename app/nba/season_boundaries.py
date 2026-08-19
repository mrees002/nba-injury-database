"""Season-specific date boundaries for fallback classification.

Defines explicit start/end dates (inclusive) for each season phase
(preseason, regular, play_in, playoffs) for seasons 2019-20 through 2025-26,
with 2026-27 preseason/regular/play_in filled and playoffs pending official dates.

Used only when no nba_schedule_games row matches an injury entry.
Derived from the NBA schedule reference data in data/reference/nba_schedule_reference.csv.
"""

from __future__ import annotations

from datetime import date
from typing import NamedTuple


class _PhaseBoundary(NamedTuple):
    start: date
    end: date


# Each entry: season -> { season_type -> (start_date, end_date) }
# Dates are inclusive.  Checked in priority order: playoffs, play_in, regular, preseason.
# 2019-20 preseason excluded due to COVID-era data anomalies.
SEASON_BOUNDARIES: dict[str, dict[str, _PhaseBoundary]] = {
    "2019-20": {
        "regular": _PhaseBoundary(date(2019, 10, 22), date(2020, 8, 14)),
        "play_in": _PhaseBoundary(date(2020, 8, 15), date(2020, 8, 15)),
        "playoffs": _PhaseBoundary(date(2020, 8, 17), date(2020, 10, 11)),
    },
    "2020-21": {
        "preseason": _PhaseBoundary(date(2020, 12, 11), date(2020, 12, 19)),
        "regular": _PhaseBoundary(date(2020, 12, 22), date(2021, 5, 16)),
        "play_in": _PhaseBoundary(date(2021, 5, 18), date(2021, 5, 21)),
        "playoffs": _PhaseBoundary(date(2021, 5, 22), date(2021, 7, 20)),
    },
    "2021-22": {
        "preseason": _PhaseBoundary(date(2021, 10, 3), date(2021, 10, 15)),
        "regular": _PhaseBoundary(date(2021, 10, 19), date(2022, 4, 10)),
        "play_in": _PhaseBoundary(date(2022, 4, 12), date(2022, 4, 15)),
        "playoffs": _PhaseBoundary(date(2022, 4, 16), date(2022, 6, 16)),
    },
    "2022-23": {
        "preseason": _PhaseBoundary(date(2022, 9, 30), date(2022, 10, 14)),
        "regular": _PhaseBoundary(date(2022, 10, 18), date(2023, 4, 9)),
        "play_in": _PhaseBoundary(date(2023, 4, 11), date(2023, 4, 14)),
        "playoffs": _PhaseBoundary(date(2023, 4, 15), date(2023, 6, 12)),
    },
    "2023-24": {
        "preseason": _PhaseBoundary(date(2023, 10, 5), date(2023, 10, 20)),
        "regular": _PhaseBoundary(date(2023, 10, 24), date(2024, 4, 14)),
        "play_in": _PhaseBoundary(date(2024, 4, 16), date(2024, 4, 19)),
        "playoffs": _PhaseBoundary(date(2024, 4, 20), date(2024, 6, 17)),
    },
    "2024-25": {
        "preseason": _PhaseBoundary(date(2024, 10, 4), date(2024, 10, 18)),
        "regular": _PhaseBoundary(date(2024, 10, 22), date(2025, 4, 13)),
        "play_in": _PhaseBoundary(date(2025, 4, 15), date(2025, 4, 18)),
        "playoffs": _PhaseBoundary(date(2025, 4, 19), date(2025, 6, 22)),
    },
    "2025-26": {
        "preseason": _PhaseBoundary(date(2025, 10, 2), date(2025, 10, 17)),
        "regular": _PhaseBoundary(date(2025, 10, 21), date(2026, 4, 12)),
        "play_in": _PhaseBoundary(date(2026, 4, 14), date(2026, 4, 17)),
        "playoffs": _PhaseBoundary(date(2026, 4, 18), date(2026, 6, 13)),
    },
    "2026-27": {
        "preseason": _PhaseBoundary(date(2026, 10, 3), date(2026, 10, 16)),
        "regular": _PhaseBoundary(date(2026, 10, 20), date(2027, 4, 11)),
        "play_in": _PhaseBoundary(date(2027, 4, 13), date(2027, 4, 16)),
        # TODO: Fill in playoffs from official NBA dates once published.
        "playoffs": None,
    },
}


def classify_by_season_boundary(
    target: date,
) -> tuple[str | None, str | None]:
    """Classify a date into (season, season_type) using season-specific boundaries.

    Returns (None, None) when the date cannot be placed into any known phase.
    """
    for season, phases in SEASON_BOUNDARIES.items():
        for season_type in ("playoffs", "play_in", "regular", "preseason"):
            boundary = phases.get(season_type)
            if boundary is not None and boundary.start <= target <= boundary.end:
                return season, season_type
    return None, None
