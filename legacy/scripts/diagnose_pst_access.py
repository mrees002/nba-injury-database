"""Run controlled, non-circumventing legacy ProSportsTransactions HTTP diagnostics.

This script records response metadata and cookie names, never cookie values. It is a
manual diagnostic and is not part of the scraper's production request path.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.scraper.query import RESULTS_URL

SEARCH_URL = "https://www.prosportstransactions.com/basketball/Search/Search.php"
IL_URL = (
    f"{RESULTS_URL}?Player=&Team=&BeginDate=2026-04-01&EndDate=2026-04-02&ILChkBx=yes&Submit=Search"
)
MISSED_GAME_URL = (
    f"{RESULTS_URL}?Player=&Team=&BeginDate=2026-04-01&EndDate=2026-04-02"
    "&InjuriesChkBx=yes&Submit=Search"
)
HONEST_USER_AGENT = "nba-injury-database/0.1 (low-frequency research client)"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
RESPONSE_HEADERS = (
    "server",
    "date",
    "content-type",
    "content-length",
    "location",
    "retry-after",
    "cf-ray",
    "cf-cache-status",
    "cf-mitigated",
)


@dataclass(frozen=True)
class Attempt:
    name: str
    url: str = IL_URL
    headers: dict[str, str] | None = None
    follow_redirects: bool = False
    http2: bool = False
    params: list[tuple[str, str]] | None = None


def _cookie_names(headers: httpx.Headers) -> list[str]:
    names: list[str] = []
    for value in headers.get_list("set-cookie"):
        name = value.partition("=")[0].strip()
        if name and name not in names:
            names.append(name)
    return names


def _safe_request_headers(headers: httpx.Headers) -> dict[str, str]:
    result = dict(headers)
    cookie = result.get("cookie")
    if cookie is not None:
        names = [part.partition("=")[0].strip() for part in cookie.split(";")]
        result["cookie"] = f"<redacted; names={','.join(names)}>"
    return result


def _response_summary(name: str, response: httpx.Response) -> dict[str, Any]:
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else None
    text = " ".join(soup.get_text(" ", strip=True).split())[:500]
    stream = response.extensions.get("network_stream")
    ssl_object = stream.get_extra_info("ssl_object") if stream is not None else None
    tls_version = ssl_object.version() if ssl_object is not None else None

    return {
        "name": name,
        "status": response.status_code,
        "url": str(response.url),
        "request_method": response.request.method,
        "request_headers": _safe_request_headers(response.request.headers),
        "http_version": response.http_version,
        "tls_version": tls_version,
        "redirect_chain": [
            {"status": item.status_code, "url": str(item.url)} for item in response.history
        ],
        "response_headers": {
            header: response.headers[header]
            for header in RESPONSE_HEADERS
            if header in response.headers
        },
        "returned_cookie_names": _cookie_names(response.headers),
        "title": title,
        "body_text": text,
        "body_bytes": len(response.content),
    }


def _error_summary(name: str, exc: Exception) -> dict[str, Any]:
    return {
        "name": name,
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


def _print(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, sort_keys=True), flush=True)


def run_attempt(attempt: Attempt) -> None:
    try:
        with httpx.Client(
            headers=attempt.headers,
            follow_redirects=attempt.follow_redirects,
            http2=attempt.http2,
            timeout=20,
        ) as client:
            response = client.get(attempt.url, params=attempt.params)
            summary = _response_summary(attempt.name, response)
        _print(summary)
    except Exception as exc:  # diagnostic must record each independent failure
        _print(_error_summary(attempt.name, exc))


def run_sequence(
    name: str,
    urls: Iterable[str],
    *,
    delay: float,
    headers: dict[str, str] | None = None,
) -> None:
    requested_urls = list(urls)
    try:
        with httpx.Client(headers=headers, follow_redirects=True, timeout=20) as client:
            for index, url in enumerate(requested_urls, start=1):
                response = client.get(url)
                _print(_response_summary(f"{name}_{index}", response))
                if index < len(requested_urls):
                    time.sleep(delay)
    except Exception as exc:  # diagnostic must record each independent failure
        _print(_error_summary(name, exc))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=5.0)
    args = parser.parse_args()
    delay = args.delay
    if delay < 0:
        parser.error("--delay must not be negative")

    attempts = [
        Attempt("plain_il"),
        Attempt("plain_missed_game", url=MISSED_GAME_URL),
        Attempt("follow_redirects", follow_redirects=True),
        Attempt("accept_html", headers={"Accept": "text/html,application/xhtml+xml"}),
        Attempt("accept_language", headers={"Accept-Language": "en-US,en;q=0.9"}),
        Attempt("accept_encoding", headers={"Accept-Encoding": "gzip, deflate"}),
        Attempt("referer", headers={"Referer": SEARCH_URL}),
        Attempt("honest_user_agent", headers={"User-Agent": HONEST_USER_AGENT}),
        Attempt("browser_user_agent", headers={"User-Agent": BROWSER_USER_AGENT}),
        Attempt("http_1_1", http2=False),
        Attempt("http_2", http2=True),
        Attempt(
            "html_form_encoding",
            url=RESULTS_URL,
            params=[
                ("Player", ""),
                ("Team", ""),
                ("BeginDate", "2026-04-01"),
                ("EndDate", "2026-04-02"),
                ("ILChkBx", "yes"),
                ("Submit", "Search"),
            ],
        ),
    ]

    for index, attempt in enumerate(attempts):
        run_attempt(attempt)
        if index < len(attempts) - 1:
            time.sleep(delay)

    run_sequence("persistent_client", [IL_URL, IL_URL], delay=delay)
    time.sleep(delay)
    run_sequence("search_then_results", [SEARCH_URL, IL_URL], delay=delay)
    time.sleep(delay)
    run_sequence(
        "ten_second_spacing",
        [IL_URL, IL_URL],
        delay=max(delay, 10),
        headers={"User-Agent": HONEST_USER_AGENT},
    )


if __name__ == "__main__":
    main()
