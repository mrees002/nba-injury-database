from __future__ import annotations

import re
import time as _time
from collections.abc import Callable, Iterable
from datetime import date, time
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from app.nba.types import DiscoveredReport

NBA_PDF_PREFIX = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_"
CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
_FILENAME_CONVENTION_CUTOFF = date(2025, 12, 22)
_FILENAME_RE = re.compile(
    r"/Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2})(?:_(\d{2}))?([AP]M)\.pdf$",
    re.IGNORECASE,
)


class NBAReportDiscoveryError(RuntimeError):
    pass


def parse_report_url(source_url: str) -> tuple[date, time]:
    match = _FILENAME_RE.search(source_url)
    if not match:
        raise NBAReportDiscoveryError(f"Unsupported NBA report URL: {source_url}")
    date_text, hour_text, minute_text, meridiem = match.groups()
    report_date = date.fromisoformat(date_text)
    hour = int(hour_text)
    minute = int(minute_text or "30")
    if not 1 <= hour <= 12 or not 0 <= minute <= 59:
        raise NBAReportDiscoveryError(f"Invalid report timestamp in URL: {source_url}")
    hour = hour % 12 + (12 if meridiem.upper() == "PM" else 0)
    return report_date, time(hour, minute)


def cdx_query_url(year: int) -> str:
    wildcard = quote(f"ak-static.cms.nba.com/referee/injury/Injury-Report_{year}-*", safe="")
    return (
        f"{CDX_ENDPOINT}?url={wildcard}&output=txt&fl=original"
        "&filter=statuscode%3A200&filter=mimetype%3Aapplication%2Fpdf&collapse=urlkey"
    )


def parse_cdx_urls(lines: Iterable[str], *, discovery_source_url: str) -> list[DiscoveredReport]:
    reports: dict[str, DiscoveredReport] = {}
    for raw_line in lines:
        source_url = raw_line.strip()
        if not source_url:
            continue
        parts = urlsplit(source_url)
        source_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        if not source_url.startswith(NBA_PDF_PREFIX):
            raise NBAReportDiscoveryError(
                f"Archive index returned a non-NBA report URL: {source_url}"
            )
        report_date, report_time = parse_report_url(source_url)
        reports[source_url] = DiscoveredReport(
            source_url=source_url,
            report_date=report_date,
            report_time=report_time,
            discovery_source_url=discovery_source_url,
        )
    return sorted(
        reports.values(), key=lambda item: (item.report_date, item.report_time, item.source_url)
    )


class CDXReportDiscovery:
    """Read URL evidence from the Internet Archive index; PDFs still come from NBA."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def discover(self, start_date: date, end_date: date) -> list[DiscoveredReport]:
        if end_date < start_date:
            raise ValueError("end_date must not be before start_date")
        reports: dict[str, DiscoveredReport] = {}
        for year in range(start_date.year, end_date.year + 1):
            source = cdx_query_url(year)
            response = self.client.get(source)
            response.raise_for_status()
            for report in parse_cdx_urls(response.text.splitlines(), discovery_source_url=source):
                if start_date <= report.report_date <= end_date:
                    reports[report.source_url] = report
        return sorted(
            reports.values(), key=lambda item: (item.report_date, item.report_time, item.source_url)
        )


def generate_candidate_urls(report_date: date) -> list[str]:
    """Return every plausible NBA injury-report PDF URL for *report_date*.

    Two filename conventions are observed in the wild, with the split
    date governed by ``_FILENAME_CONVENTION_CUTOFF`` (2025-12-22):

    Before the cutoff only the hour-only convention is generated:

    1. ``HHAM.pdf`` / ``HHPM.pdf`` – hour only, implied :30 minutes.

    On and after the cutoff only explicit-minute variants are generated:

    2. ``HH_00AM.pdf`` / ``HH_00PM.pdf`` – explicit :00 minutes.
    3. ``HH_15AM.pdf`` / ``HH_15PM.pdf`` – explicit :15 minutes.
    4. ``HH_30AM.pdf`` / ``HH_30PM.pdf`` – explicit :30 minutes.
    5. ``HH_45AM.pdf`` / ``HH_45PM.pdf`` – explicit :45 minutes.

    The function is pure (no network requests) and returns a
    deterministic, sorted list so callers can iterate in a stable order.
    """
    date_part = report_date.isoformat()
    urls: list[str] = []
    use_new_convention = report_date >= _FILENAME_CONVENTION_CUTOFF
    if not use_new_convention:
        for hour_12 in range(1, 13):
            hh = f"{hour_12:02d}"
            for meridiem in ("AM", "PM"):
                urls.append(f"{NBA_PDF_PREFIX}{date_part}_{hh}{meridiem}.pdf")
    else:
        for hour_12 in range(1, 13):
            hh = f"{hour_12:02d}"
            for minute in ("00", "15", "30", "45"):
                for meridiem in ("AM", "PM"):
                    urls.append(f"{NBA_PDF_PREFIX}{date_part}_{hh}_{minute}{meridiem}.pdf")
    return urls


def probe_candidate_urls(
    candidate_urls: list[str],
    client: httpx.Client,
    *,
    request_interval_seconds: float = 0,
    sleep: Callable[[float], None] = _time.sleep,
    monotonic: Callable[[], float] = _time.monotonic,
) -> list[str]:
    """Return the subset of *candidate_urls* that resolve to a valid PDF on the NBA host.

    For each URL a lightweight HEAD is issued first.  If the server rejects
    HEAD (405) a short GET with ``Range: bytes=0-65535`` is used instead so
    we never download an entire multi-page report just to check existence.

    A URL is considered valid when:

    * the HTTP status is 200,
    * the ``Content-Type`` is ``application/pdf``, and
    * the first bytes of the body contain the ``%PDF-`` signature.

    Results are returned sorted for deterministic downstream processing.
    """
    valid_urls: list[str] = []
    last_request_at: float | None = None
    for url in candidate_urls:
        if request_interval_seconds > 0 and last_request_at is not None:
            wait = request_interval_seconds - (monotonic() - last_request_at)
            if wait > 0:
                sleep(wait)
        try:
            head = client.head(url)
            if head.status_code == 405:
                resp = client.get(url, headers={"Range": "bytes=0-65535"})
            elif head.status_code == 200:
                resp = head
            else:
                last_request_at = monotonic()
                continue
        except httpx.HTTPError:
            last_request_at = monotonic()
            continue

        last_request_at = monotonic()

        content_type = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/pdf":
            continue

        # HEAD responses carry no body, so the PDF signature check only
        # applies when we have actual content (GET fallback or full GET).
        if resp.content and not resp.content.startswith(b"%PDF-"):
            continue

        valid_urls.append(url)

    return sorted(valid_urls)


def discover_from_manifest(
    manifest_text: str, *, source: str = "local-manifest"
) -> list[DiscoveredReport]:
    """Load a saved URL manifest for reproducible/offline historical runs."""

    return parse_cdx_urls(manifest_text.splitlines(), discovery_source_url=source)
