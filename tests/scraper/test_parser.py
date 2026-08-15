from datetime import date
from pathlib import Path

import pytest

from app.scraper.exceptions import ResultsStructureError
from app.scraper.parser import parse_results_page

FIXTURES = Path(__file__).parents[1] / "fixtures" / "scraper"
IL_URL = (
    "https://www.prosportstransactions.com/basketball/Search/SearchResults.php"
    "?Player=&Team=&BeginDate=2026-04-01&EndDate=2026-04-02"
    "&ILChkBx=yes&Submit=Search"
)
MISSED_GAME_URL = (
    "https://www.prosportstransactions.com/basketball/Search/SearchResults.php"
    "?Player=&Team=&BeginDate=2026-04-01&EndDate=2026-04-02"
    "&InjuriesChkBx=yes&Submit=Search"
)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_parse_il_results_normalizes_whitespace_and_preserves_source_url() -> None:
    result = parse_results_page(
        fixture("il_results_page_1.html"),
        source_type="il",
        source_url=IL_URL,
    )

    assert len(result.records) == 2
    assert result.records[0].transaction_date == date(2026, 4, 1)
    assert result.records[0].team == "76ers"
    assert result.records[0].acquired == ""
    assert result.records[0].relinquished == "• Joel Embiid"
    assert result.records[0].notes == "placed on IL with illness"
    assert result.records[0].source_type == "il"
    assert result.records[0].source_url == IL_URL
    assert result.next_url == f"{IL_URL}&start=25"


def test_parse_missed_game_results() -> None:
    result = parse_results_page(
        fixture("missed_game_results.html"),
        source_type="missed_game",
        source_url=MISSED_GAME_URL,
    )

    assert [(record.acquired, record.relinquished, record.notes) for record in result.records] == [
        ("", "• Luka Doncic", "missed game with left leg injury"),
        ("• Luka Doncic", "", "returned to lineup"),
    ]
    assert all(record.source_type == "missed_game" for record in result.records)
    assert all(record.source_url == MISSED_GAME_URL for record in result.records)
    assert result.next_url is None


def test_parse_explicit_empty_results() -> None:
    result = parse_results_page(fixture("empty_results.html"), source_type="il", source_url=IL_URL)

    assert result.records == []
    assert result.next_url is None


@pytest.mark.parametrize("fixture_name", ["changed_headers.html", "malformed_row.html"])
def test_parse_fails_loudly_for_changed_or_malformed_table(fixture_name: str) -> None:
    with pytest.raises(ResultsStructureError):
        parse_results_page(fixture(fixture_name), source_type="il", source_url=IL_URL)


def test_parse_rejects_unknown_page_instead_of_treating_it_as_empty() -> None:
    with pytest.raises(ResultsStructureError, match="expected results table"):
        parse_results_page(
            "<html><body>Unexpected response</body></html>",
            source_type="il",
            source_url=IL_URL,
        )
