from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from app.scraper.types import SourceType

RESULTS_URL = "https://www.prosportstransactions.com/basketball/Search/SearchResults.php"
SOURCE_CHECKBOX_PARAMETERS = {
    "il": "ILChkBx",
    "missed_game": "InjuriesChkBx",
}


def build_search_url(source_type: SourceType, start_date: date, end_date: date) -> str:
    if source_type not in SOURCE_CHECKBOX_PARAMETERS:
        raise ValueError(f"unsupported source type: {source_type!r}")
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")

    parameters = [
        ("Player", ""),
        ("Team", ""),
        ("BeginDate", start_date.isoformat()),
        ("EndDate", end_date.isoformat()),
        (SOURCE_CHECKBOX_PARAMETERS[source_type], "yes"),
        ("Submit", "Search"),
    ]
    return f"{RESULTS_URL}?{urlencode(parameters)}"
