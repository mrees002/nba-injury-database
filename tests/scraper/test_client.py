import httpx
import pytest

from app.scraper.client import HTTPClientConfig, ScraperHTTPClient
from app.scraper.exceptions import AccessRestrictedError, ScraperRequestError

URL = "https://www.prosportstransactions.com/basketball/Search/SearchResults.php"


def test_client_retries_temporary_failures_with_bounded_exponential_backoff() -> None:
    responses = iter([503, 503, 200])
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(next(responses), text="ok")

    config = HTTPClientConfig(
        user_agent="nba-injury-test/1.0",
        request_interval_seconds=0,
        max_retries=2,
        backoff_base_seconds=0.5,
        max_backoff_seconds=0.75,
    )
    with ScraperHTTPClient(
        config, transport=httpx.MockTransport(handler), sleep=sleeps.append
    ) as client:
        response = client.get(URL)

    assert response.status_code == 200
    assert len(requests) == 3
    assert sleeps == [0.5, 0.75]
    assert requests[0].headers["User-Agent"] == "nba-injury-test/1.0"


def test_client_stops_after_bounded_retry_count() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, text="slow down")

    config = HTTPClientConfig(
        user_agent="nba-injury-test/1.0",
        request_interval_seconds=0,
        max_retries=1,
        backoff_base_seconds=0,
    )
    with ScraperHTTPClient(config, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ScraperRequestError, match="after 2 attempts"):
            client.get(URL)

    assert calls == 2


@pytest.mark.parametrize(
    ("status_code", "body"),
    [(403, "Forbidden"), (200, "<title>Just a moment...</title><div id='cf-chl-widget'></div>")],
)
def test_client_does_not_retry_or_bypass_access_restrictions(
    status_code: int,
    body: str,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, text=body)

    config = HTTPClientConfig(
        user_agent="nba-injury-test/1.0",
        request_interval_seconds=0,
        max_retries=3,
    )
    with ScraperHTTPClient(config, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AccessRestrictedError):
            client.get(URL)

    assert calls == 1


def test_client_stops_on_cloudflare_challenge_header_before_temporary_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, headers={"Cf-Mitigated": "challenge"}, text="unavailable")

    config = HTTPClientConfig(
        user_agent="nba-injury-test/1.0",
        request_interval_seconds=0,
        max_retries=3,
    )
    with ScraperHTTPClient(config, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AccessRestrictedError):
            client.get(URL)

    assert calls == 1


def test_client_enforces_minimum_interval_between_requests() -> None:
    now = 10.0
    sleeps: list[float] = []

    def monotonic() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    config = HTTPClientConfig(user_agent="nba-injury-test/1.0", request_interval_seconds=2)
    with ScraperHTTPClient(
        config,
        transport=httpx.MockTransport(handler),
        sleep=sleep,
        monotonic=monotonic,
    ) as client:
        client.get(URL)
        client.get(URL)

    assert sleeps == [2.0]
