from __future__ import annotations

from datetime import date
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.scraper.exceptions import ResultsStructureError
from app.scraper.query import RESULTS_URL
from app.scraper.types import ParsedResultsPage, ScrapedTransaction, SourceType

EXPECTED_HEADERS = ["Date", "Team", "Acquired", "Relinquished", "Notes"]
EMPTY_RESULTS_TEXT = "There were no matching transactions found."


def normalize_whitespace(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _cell_text(cell: Tag) -> str:
    return normalize_whitespace(cell.get_text(" ", strip=True))


def _row_cells(row: Tag) -> list[Tag]:
    return row.find_all(["th", "td"], recursive=False)


def _results_table(soup: BeautifulSoup) -> Tag | None:
    tables = soup.select("table.datatable.center")
    for table in tables:
        first_row = table.find("tr")
        if first_row is None:
            continue
        if [_cell_text(cell) for cell in _row_cells(first_row)] == EXPECTED_HEADERS:
            return table

    if tables:
        observed_headers = []
        first_row = tables[0].find("tr")
        if first_row is not None:
            observed_headers = [_cell_text(cell) for cell in _row_cells(first_row)]
        raise ResultsStructureError(
            f"results table headers changed: expected {EXPECTED_HEADERS}, got {observed_headers}"
        )
    return None


def _next_url(soup: BeautifulSoup, source_url: str) -> str | None:
    next_link = next(
        (
            link
            for link in soup.find_all("a", href=True)
            if normalize_whitespace(link.get_text(" ", strip=True)) == "Next"
        ),
        None,
    )
    if next_link is None:
        return None

    next_url = urljoin(source_url, str(next_link["href"]))
    parsed_next = urlparse(next_url)
    parsed_results = urlparse(RESULTS_URL)
    if (
        parsed_next.scheme != "https"
        or parsed_next.hostname != parsed_results.hostname
        or parsed_next.path != parsed_results.path
    ):
        raise ResultsStructureError(f"unexpected pagination URL: {next_url}")
    return next_url


def parse_results_page(
    html: str,
    *,
    source_type: SourceType,
    source_url: str,
) -> ParsedResultsPage:
    soup = BeautifulSoup(html, "html.parser")
    page_text = normalize_whitespace(soup.get_text(" ", strip=True))
    table = _results_table(soup)

    if table is None:
        if EMPTY_RESULTS_TEXT in page_text:
            return ParsedResultsPage(records=[], next_url=None)
        raise ResultsStructureError("expected results table or explicit empty-results message")

    rows = table.find_all("tr")
    records: list[ScrapedTransaction] = []
    for row_number, row in enumerate(rows[1:], start=2):
        cells = _row_cells(row)
        if len(cells) != len(EXPECTED_HEADERS):
            raise ResultsStructureError(
                f"results row {row_number} has {len(cells)} cells; expected 5"
            )
        values = [_cell_text(cell) for cell in cells]
        try:
            transaction_date = date.fromisoformat(values[0])
        except ValueError as exc:
            raise ResultsStructureError(
                f"results row {row_number} has invalid date {values[0]!r}"
            ) from exc
        records.append(
            ScrapedTransaction(
                source_type=source_type,
                transaction_date=transaction_date,
                team=values[1],
                acquired=values[2],
                relinquished=values[3],
                notes=values[4],
                source_url=source_url,
            )
        )

    return ParsedResultsPage(records=records, next_url=_next_url(soup, source_url))
