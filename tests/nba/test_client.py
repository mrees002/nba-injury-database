from __future__ import annotations

import httpx
import pytest

from app.nba.client import NBAReportClient, NBAReportValidationError


def test_download_validates_pdf_and_preserves_url():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == "fixture-agent"
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.4\nfixture",
            request=request,
        )

    with NBAReportClient(
        user_agent="fixture-agent",
        request_interval_seconds=0,
        transport=httpx.MockTransport(handler),
    ) as client:
        report = client.download("https://ak-static.cms.nba.com/referee/injury/test.pdf")

    assert report.source_url.endswith("/test.pdf")
    assert len(report.content_hash) == 64


@pytest.mark.parametrize(
    ("content_type", "content", "message"),
    [
        ("text/html", b"%PDF-1.4", "Expected application/pdf"),
        ("application/pdf", b"not a pdf", "lacks the PDF signature"),
    ],
)
def test_download_rejects_wrong_content_type_or_signature(content_type, content, message):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": content_type},
            content=content,
            request=request,
        )
    )
    with NBAReportClient(
        user_agent="fixture-agent", request_interval_seconds=0, transport=transport
    ) as client:
        with pytest.raises(NBAReportValidationError, match=message):
            client.download("https://ak-static.cms.nba.com/referee/injury/test.pdf")


def test_retry_is_bounded_and_exponential():
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, request=request)

    with NBAReportClient(
        user_agent="fixture-agent",
        request_interval_seconds=0,
        max_retries=2,
        backoff_base_seconds=0.5,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    ) as client:
        with pytest.raises(RuntimeError, match="3 attempts"):
            client.download("https://ak-static.cms.nba.com/referee/injury/test.pdf")

    assert attempts == 3
    assert sleeps == [0.5, 1.0]


def test_download_403_raises_immediately_without_retry():
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(403, request=request)

    with NBAReportClient(
        user_agent="fixture-agent",
        request_interval_seconds=0,
        max_retries=3,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    ) as client:
        with pytest.raises(NBAReportValidationError, match="Non-retryable HTTP 403"):
            client.download("https://ak-static.cms.nba.com/referee/injury/test.pdf")

    assert attempts == 1
    assert sleeps == []


def test_download_401_raises_immediately_without_retry():
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, request=request)

    with NBAReportClient(
        user_agent="fixture-agent",
        request_interval_seconds=0,
        max_retries=3,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    ) as client:
        with pytest.raises(NBAReportValidationError, match="Non-retryable HTTP 401"):
            client.download("https://ak-static.cms.nba.com/referee/injury/test.pdf")

    assert attempts == 1
    assert sleeps == []


def test_download_206_assembles_full_content():
    pdf_content = b"%PDF-1.4\n" + b"x" * 100_000
    total_size = len(pdf_content)
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        range_header = request.headers.get("range", "")
        if request_count == 1:
            assert range_header == "bytes=0-65535"
            return httpx.Response(
                206,
                headers={
                    "Content-Type": "application/pdf",
                    "Content-Range": f"bytes 0-65535/{total_size}",
                },
                content=pdf_content[:65536],
                request=request,
            )
        assert range_header == f"bytes=0-{total_size - 1}"
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=pdf_content,
            request=request,
        )

    with NBAReportClient(
        user_agent="fixture-agent",
        request_interval_seconds=0,
        transport=httpx.MockTransport(handler),
    ) as client:
        report = client.download("https://ak-static.cms.nba.com/referee/injury/test.pdf")

    assert report.content == pdf_content
    assert len(report.content) == total_size
    assert request_count == 2


def test_download_206_no_second_request_when_body_complete():
    pdf_content = b"%PDF-1.4\nsmall"
    total_size = len(pdf_content)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            206,
            headers={
                "Content-Type": "application/pdf",
                "Content-Range": f"bytes 0-{total_size - 1}/{total_size}",
            },
            content=pdf_content,
            request=request,
        )

    with NBAReportClient(
        user_agent="fixture-agent",
        request_interval_seconds=0,
        transport=httpx.MockTransport(handler),
    ) as client:
        report = client.download("https://ak-static.cms.nba.com/referee/injury/test.pdf")

    assert report.content == pdf_content


def test_download_206_incomplete_triggers_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            206,
            headers={
                "Content-Type": "application/pdf",
                "Content-Range": "bytes 0-65535/200000",
            },
            content=b"%PDF-" + b"\x00" * 65531,
            request=request,
        )

    with NBAReportClient(
        user_agent="fixture-agent",
        request_interval_seconds=0,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(NBAReportValidationError, match="Incomplete download"):
            client.download("https://ak-static.cms.nba.com/referee/injury/test.pdf")
