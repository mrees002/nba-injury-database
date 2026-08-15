from __future__ import annotations

from typing import Protocol

import httpx

from app.scraper.exceptions import PaginationError
from app.scraper.parser import parse_results_page
from app.scraper.types import ScrapedTransaction, SourceType


class PageClient(Protocol):
    def get(self, url: str) -> httpx.Response: ...


def fetch_all_pages(
    client: PageClient,
    *,
    source_type: SourceType,
    first_url: str,
    max_pages: int,
) -> list[ScrapedTransaction]:
    if max_pages <= 0:
        raise ValueError("max_pages must be positive")

    records: list[ScrapedTransaction] = []
    seen_urls: set[str] = set()
    page_url: str | None = first_url
    page_count = 0

    while page_url is not None:
        if page_url in seen_urls:
            raise PaginationError(f"pagination loop detected at {page_url}")
        if page_count >= max_pages:
            raise PaginationError(f"pagination exceeded configured maximum of {max_pages} pages")

        seen_urls.add(page_url)
        page_count += 1
        response = client.get(page_url)
        source_url = str(response.url)
        parsed = parse_results_page(
            response.text,
            source_type=source_type,
            source_url=source_url,
        )
        records.extend(parsed.records)
        page_url = parsed.next_url

    return records
