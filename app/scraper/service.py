from __future__ import annotations

from datetime import date

from app.config import get_settings
from app.scraper.client import HTTPClientConfig, ScraperHTTPClient
from app.scraper.pagination import PageClient, fetch_all_pages
from app.scraper.query import build_search_url
from app.scraper.types import ScrapedTransaction, SourceType


def scrape_transactions(
    source_type: SourceType,
    start_date: date,
    end_date: date,
    *,
    client: PageClient | None = None,
    max_pages: int | None = None,
) -> list[ScrapedTransaction]:
    first_url = build_search_url(source_type, start_date, end_date)
    settings = get_settings()
    page_limit = settings.scraper_max_pages if max_pages is None else max_pages

    if client is not None:
        return fetch_all_pages(
            client,
            source_type=source_type,
            first_url=first_url,
            max_pages=page_limit,
        )

    config = HTTPClientConfig(
        user_agent=settings.scraper_user_agent,
        timeout_seconds=settings.scraper_timeout_seconds,
        request_interval_seconds=settings.scraper_request_interval_seconds,
        max_retries=settings.scraper_max_retries,
        backoff_base_seconds=settings.scraper_backoff_base_seconds,
    )
    with ScraperHTTPClient(config) as scraper_client:
        return fetch_all_pages(
            scraper_client,
            source_type=source_type,
            first_url=first_url,
            max_pages=page_limit,
        )
