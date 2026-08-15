# ProSportsTransactions scraper contract

> **Production status: BLOCKED.** The query builder and offline-tested parser/client are complete,
> but no permitted, reproducible acquisition method has been proved from a realistic scheduler.
> See [the Prompt 4 access assessment](prompt4_access_assessment.md).

This contract was inspected against the live ProSportsTransactions basketball pages on
2026-08-11 before the selectors and query builder were implemented.

## Search requests

The basketball search form is:

`https://www.prosportstransactions.com/basketball/Search/Search.php`

It submits a GET request to:

`https://www.prosportstransactions.com/basketball/Search/SearchResults.php`

The common parameters are:

| Parameter | Value |
| --- | --- |
| `Player` | empty |
| `Team` | empty |
| `BeginDate` | inclusive `YYYY-MM-DD` date |
| `EndDate` | inclusive `YYYY-MM-DD` date |
| `Submit` | `Search` |

The source-specific checkbox is `ILChkBx=yes` for movement to or from the injured/inactive list,
and `InjuriesChkBx=yes` for missed games due to injury. For example:

```text
https://www.prosportstransactions.com/basketball/Search/SearchResults.php?Player=&Team=&BeginDate=2026-04-01&EndDate=2026-04-02&ILChkBx=yes&Submit=Search
https://www.prosportstransactions.com/basketball/Search/SearchResults.php?Player=&Team=&BeginDate=2026-04-01&EndDate=2026-04-02&InjuriesChkBx=yes&Submit=Search
```

## Result table and pagination

Results are in `table.datatable.center`. The first row has exactly five cells with the headers
`Date`, `Team`, `Acquired`, `Relinquished`, and `Notes`; subsequent rows have exactly five cells in
that order. Dates are ISO `YYYY-MM-DD`. The parser collapses whitespace while retaining field text,
including the source's leading player bullet (`•`). A missing table is accepted only when the page
contains the explicit message `There were no matching transactions found.` All other missing,
renamed, or malformed structures raise a clear `ResultsStructureError`.

The live site returned 25 results per page. Its `Next` link retains the search query and adds a
zero-based `start` offset in increments of 25. The second page of the example IL query was:

```text
https://www.prosportstransactions.com/basketball/Search/SearchResults.php?Player=&Team=&BeginDate=2026-04-01&EndDate=2026-04-02&ILChkBx=yes&Submit=Search&start=25
```

The scraper follows the site's observed `Next` URL instead of synthesizing offsets. It stops when
there is no linked `Next`, rejects off-site or unexpected pagination URLs, detects URL loops, and
enforces a configurable maximum page count.

## Network behavior and access limits

The HTTP client sends the configured User-Agent, uses a 30-second timeout, and leaves at least two
seconds between request starts by default. Transport errors and HTTP 429, 500, 502, 503, and 504
responses receive at most three retries after the initial attempt. Retry delays double from one
second and are capped at eight seconds. HTTP 401/403 and recognizable CAPTCHA or access-challenge
pages fail immediately with `AccessRestrictedError`; no bypass is attempted.

Interactive live inspection succeeded after Cloudflare displayed and then automatically cleared a
“Just a moment” page. Controlled direct requests received a Cloudflare managed challenge with HTTP
403 for every ordinary HTTP variation tested. A read-only request for `robots.txt` also received
HTTP 403. Do not deploy recurring scraping unless the site owner provides permission and a
reproducible access mechanism. The code does not use browser cookies or challenge-solving
techniques.

## Offline coverage

Small sanitized HTML fixtures cover both source types, a two-page result using the observed Next
link, an empty result, whitespace normalization, source URL lineage, a changed header, and a
malformed row. Mock-transport tests cover User-Agent delivery, conservative rate limiting, bounded
temporary-failure retries, and immediate failure on access restrictions. Tests do not require or
perform live network access.
