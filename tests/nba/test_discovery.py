from __future__ import annotations

from datetime import date, time
from unittest.mock import MagicMock

import httpx
import pytest

from app.nba.discovery import (
    NBAReportDiscoveryError,
    cdx_query_url,
    generate_candidate_urls,
    parse_cdx_urls,
    parse_report_url,
    probe_candidate_urls,
)


def test_parses_both_filename_conventions():
    assert parse_report_url(
        "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2024-04-12_05PM.pdf"
    ) == (date(2024, 4, 12), time(17, 30))
    assert parse_report_url(
        "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-04-12_05_00PM.pdf"
    ) == (date(2026, 4, 12), time(17, 0))


def test_cdx_parser_deduplicates_and_rejects_non_official_urls():
    url = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2021-04-18_08PM.pdf"
    reports = parse_cdx_urls(
        [url, f"{url}?utm_source=example", ""], discovery_source_url="fixture:index"
    )
    assert len(reports) == 1
    assert reports[0].source_url == url
    assert reports[0].discovery_source_url == "fixture:index"

    with pytest.raises(NBAReportDiscoveryError, match="non-NBA"):
        parse_cdx_urls(["https://example.com/report.pdf"], discovery_source_url="fixture:index")


def test_cdx_query_is_bounded_to_one_year():
    url = cdx_query_url(2024)
    assert "Injury-Report_2024-%2A" in url
    assert "collapse=urlkey" in url


def test_generate_candidate_urls_historical_date():
    urls = generate_candidate_urls(date(2024, 4, 12))
    # Only hour-only convention (implied :30)
    assert any("2024-04-12_05AM.pdf" in u for u in urls)
    assert any("2024-04-12_05PM.pdf" in u for u in urls)
    # Explicit minute variants must NOT appear
    assert not any("_00" in u for u in urls)
    assert not any("_15" in u for u in urls)
    # 12 hours × 2 meridiems = 24
    assert len(urls) == 24


def test_generate_candidate_urls_new_convention():
    urls = generate_candidate_urls(date(2026, 6, 3))
    # Hour-only convention must NOT appear
    assert not any("2026-06-03_05AM.pdf" in u for u in urls)
    assert not any("2026-06-03_05PM.pdf" in u for u in urls)
    # Explicit :00, :15, :30, :45 variants
    assert any("2026-06-03_05_00AM.pdf" in u for u in urls)
    assert any("2026-06-03_05_15PM.pdf" in u for u in urls)
    assert any("2026-06-03_05_30AM.pdf" in u for u in urls)
    assert any("2026-06-03_05_45PM.pdf" in u for u in urls)
    # 12 hours × 2 meridiems × 4 minutes = 96
    assert len(urls) == 96


def test_probe_candidate_urls_respects_request_interval():
    """probe_candidate_urls spaces requests by request_interval_seconds."""
    pdf_content = b"%PDF-1.4\nfixture"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=pdf_content,
            request=request,
        )
    )
    client = httpx.Client(transport=transport, follow_redirects=True)
    urls = [
        "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-06-03_05_00PM.pdf",
        "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-06-03_05_15PM.pdf",
        "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-06-03_05_30PM.pdf",
    ]

    fake_time = MagicMock(return_value=0.0)
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        fake_time.return_value += seconds

    # With 1.0s interval, each probe after the first should wait ~1.0s
    result = probe_candidate_urls(
        urls,
        client,
        request_interval_seconds=1.0,
        sleep=fake_sleep,
        monotonic=fake_time,
    )

    assert len(result) == 3
    # 2 waits for 3 URLs (first request has no prior timestamp)
    assert len(sleep_calls) == 2
    assert all(wait >= 1.0 for wait in sleep_calls)


def test_probe_candidate_urls_no_wait_when_interval_zero():
    """With request_interval_seconds=0 no sleeping occurs."""
    pdf_content = b"%PDF-1.4\nfixture"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=pdf_content,
            request=request,
        )
    )
    client = httpx.Client(transport=transport, follow_redirects=True)
    urls = [
        "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-06-03_05_00PM.pdf",
        "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-06-03_05_15PM.pdf",
    ]

    sleep_calls: list[float] = []
    result = probe_candidate_urls(
        urls,
        client,
        request_interval_seconds=0,
        sleep=lambda s: sleep_calls.append(s),
    )

    assert len(result) == 2
    assert sleep_calls == []
