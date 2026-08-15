from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.scraper.exceptions import AccessRestrictedError, ScraperRequestError

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
ACCESS_RESTRICTED_STATUS_CODES = frozenset({401, 403})


@dataclass(frozen=True)
class HTTPClientConfig:
    user_agent: str
    timeout_seconds: float = 30.0
    request_interval_seconds: float = 2.0
    max_retries: int = 3
    backoff_base_seconds: float = 1.0
    max_backoff_seconds: float = 8.0

    def __post_init__(self) -> None:
        if not self.user_agent.strip():
            raise ValueError("user_agent must not be blank")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.request_interval_seconds < 0:
            raise ValueError("request_interval_seconds must not be negative")
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if self.backoff_base_seconds < 0 or self.max_backoff_seconds < 0:
            raise ValueError("backoff values must not be negative")


class ScraperHTTPClient:
    def __init__(
        self,
        config: HTTPClientConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_started_at: float | None = None
        self._client = httpx.Client(
            headers={"User-Agent": config.user_agent},
            timeout=config.timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )

    def __enter__(self) -> ScraperHTTPClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _rate_limit(self) -> None:
        if self._last_request_started_at is not None:
            elapsed = self._monotonic() - self._last_request_started_at
            remaining = self.config.request_interval_seconds - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_started_at = self._monotonic()

    def _backoff(self, attempt: int) -> None:
        delay = min(
            self.config.backoff_base_seconds * (2**attempt),
            self.config.max_backoff_seconds,
        )
        if delay > 0:
            self._sleep(delay)

    @staticmethod
    def _looks_like_access_challenge(response: httpx.Response) -> bool:
        if response.headers.get("cf-mitigated", "").lower() == "challenge":
            return True
        body = response.text.lower()
        return (
            "just a moment" in body
            or "cf-chl-" in body
            or "captcha" in body
            or "verify you are human" in body
        )

    def get(self, url: str) -> httpx.Response:
        for attempt in range(self.config.max_retries + 1):
            self._rate_limit()
            try:
                response = self._client.get(url)
            except httpx.TransportError as exc:
                if attempt == self.config.max_retries:
                    raise ScraperRequestError(
                        f"request failed after {attempt + 1} attempts: {url}"
                    ) from exc
                self._backoff(attempt)
                continue

            if response.status_code in ACCESS_RESTRICTED_STATUS_CODES:
                raise AccessRestrictedError(
                    f"site returned access-restricted status {response.status_code}: {url}"
                )
            if self._looks_like_access_challenge(response):
                raise AccessRestrictedError(f"site returned an access challenge: {url}")
            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt == self.config.max_retries:
                    raise ScraperRequestError(
                        f"site returned {response.status_code} after {attempt + 1} attempts: {url}"
                    )
                self._backoff(attempt)
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ScraperRequestError(
                    f"site returned non-success status {response.status_code}: {url}"
                ) from exc
            return response

        raise AssertionError("retry loop exited unexpectedly")
