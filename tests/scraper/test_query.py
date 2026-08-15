from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from app.scraper.query import RESULTS_URL, build_search_url


@pytest.mark.parametrize(
    ("source_type", "checkbox"),
    [("il", "ILChkBx"), ("missed_game", "InjuriesChkBx")],
)
def test_build_search_url_uses_observed_get_parameters(
    source_type: str,
    checkbox: str,
) -> None:
    url = build_search_url(source_type, date(2026, 4, 1), date(2026, 4, 2))  # type: ignore[arg-type]
    parsed = urlparse(url)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == RESULTS_URL
    assert parse_qs(parsed.query, keep_blank_values=True) == {
        "Player": [""],
        "Team": [""],
        "BeginDate": ["2026-04-01"],
        "EndDate": ["2026-04-02"],
        checkbox: ["yes"],
        "Submit": ["Search"],
    }
    assert url == (
        f"{RESULTS_URL}?Player=&Team=&BeginDate=2026-04-01&EndDate=2026-04-02"
        f"&{checkbox}=yes&Submit=Search"
    )


def test_build_search_url_rejects_reverse_date_range() -> None:
    with pytest.raises(ValueError, match="start_date"):
        build_search_url("il", date(2026, 4, 2), date(2026, 4, 1))


def test_build_search_url_rejects_unknown_source_type() -> None:
    with pytest.raises(ValueError, match="unsupported source type"):
        build_search_url("transactions", date(2026, 4, 1), date(2026, 4, 2))  # type: ignore[arg-type]
