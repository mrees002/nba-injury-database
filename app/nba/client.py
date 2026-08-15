from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from app.nba.types import DownloadedReport


class NBAReportHTTPError(RuntimeError):
    pass


class NBAReportMissingError(NBAReportHTTPError):
    pass


class NBAReportValidationError(NBAReportHTTPError):
    pass


class NBAReportClient:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 30,
        request_interval_seconds: float = 1.0,
        max_retries: int = 3,
        backoff_base_seconds: float = 1,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("A descriptive User-Agent is required")
        self.request_interval_seconds = request_interval_seconds
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.sleep = sleep
        self.monotonic = monotonic
        self._last_request_at: float | None = None
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/pdf"},
            timeout=timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )

    def __enter__(self) -> NBAReportClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    def _rate_limit(self) -> None:
        if self._last_request_at is not None:
            wait = self.request_interval_seconds - (self.monotonic() - self._last_request_at)
            if wait > 0:
                self.sleep(wait)

    def download(self, source_url: str) -> DownloadedReport:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._rate_limit()
            try:
                response = self.client.get(
                    source_url, headers={"Range": "bytes=0-65535"}
                )
                self._last_request_at = self.monotonic()
                if response.status_code == 404:
                    raise NBAReportMissingError(f"NBA report not found: {source_url}")
                if response.status_code in (401, 403):
                    raise NBAReportValidationError(
                        f"Non-retryable HTTP {response.status_code} for {source_url}"
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    raise NBAReportHTTPError(
                        f"Retryable HTTP {response.status_code} for {source_url}"
                    )
                if response.status_code == 206:
                    content_range = response.headers.get("content-range", "")
                    if "/" in content_range:
                        total_size = int(content_range.split("/", 1)[1])
                        if total_size > len(response.content):
                            response = self.client.get(
                                source_url,
                                headers={"Range": f"bytes=0-{total_size - 1}"},
                            )
                            self._last_request_at = self.monotonic()
                            if len(response.content) != total_size:
                                raise NBAReportValidationError(
                                    f"Incomplete download: expected {total_size} bytes, "
                                    f"got {len(response.content)}: {source_url}"
                                )
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type != "application/pdf":
                    raise NBAReportValidationError(
                        f"Expected application/pdf, got {content_type or '<missing>'}: {source_url}"
                    )
                content = response.content
                if not content.startswith(b"%PDF-"):
                    raise NBAReportValidationError(
                        f"Response lacks the PDF signature: {source_url}"
                    )
                return DownloadedReport(
                    source_url=str(response.url),
                    content=content,
                    content_type=content_type,
                    downloaded_at=datetime.now(UTC),
                    content_hash=hashlib.sha256(content).hexdigest(),
                )
            except NBAReportMissingError:
                raise
            except NBAReportValidationError:
                raise
            except (httpx.HTTPError, NBAReportHTTPError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self.sleep(self.backoff_base_seconds * (2**attempt))
        raise NBAReportHTTPError(
            f"NBA report download failed after {self.max_retries + 1} attempts: {source_url}"
        ) from last_error
