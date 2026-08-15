from datetime import date
from pathlib import Path

import httpx
import pytest

from app.scraper.exceptions import PaginationError
from app.scraper.pagination import fetch_all_pages
from app.scraper.query import build_search_url
from app.scraper.service import scrape_transactions

FIXTURES = Path(__file__).parents[1] / "fixtures" / "scraper"


class FixtureClient:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url: str) -> httpx.Response:
        self.calls.append(url)
        request = httpx.Request("GET", url)
        return httpx.Response(200, text=self.pages[url], request=request)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_scrape_transactions_follows_observed_next_link() -> None:
    first_url = build_search_url("il", date(2026, 4, 1), date(2026, 4, 2))
    second_url = f"{first_url}&start=25"
    client = FixtureClient(
        {
            first_url: fixture("il_results_page_1.html"),
            second_url: fixture("il_results_page_2.html"),
        }
    )

    records = scrape_transactions(
        "il",
        date(2026, 4, 1),
        date(2026, 4, 2),
        client=client,
    )

    assert client.calls == [first_url, second_url]
    assert len(records) == 3
    assert [record.source_url for record in records] == [first_url, first_url, second_url]


def test_fetch_all_pages_stops_at_configured_maximum() -> None:
    first_url = build_search_url("il", date(2026, 4, 1), date(2026, 4, 2))
    client = FixtureClient({first_url: fixture("il_results_page_1.html")})

    with pytest.raises(PaginationError, match="configured maximum"):
        fetch_all_pages(client, source_type="il", first_url=first_url, max_pages=1)


def test_scrape_transactions_rejects_zero_page_limit() -> None:
    client = FixtureClient({})

    with pytest.raises(ValueError, match="max_pages"):
        scrape_transactions("il", date(2026, 4, 1), date(2026, 4, 2), client=client, max_pages=0)
