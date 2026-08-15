# Prompt 4 production-access assessment

Assessment date: 2026-08-11

Status: **BLOCKED**. The existing request builder, parser, pagination, typed records, and defensive
HTTP client remain useful, but none of the approved success criteria has been met:

- Ordinary HTTP cannot retrieve either source type.
- An interactive browser can retrieve both source types only after encountering Cloudflare's
  JavaScript/cookie challenge; this was not proved from a clean scheduled deployment environment.
- No official API, export, feed, or approved access mechanism was found.

Prompt 5 must not schedule or ingest live ProSportsTransactions data until this status is resolved.

## Root cause of HTTP 403

The 403 originates from Cloudflare's managed challenge, not from the application search handler or
an invalid query. Evidence from direct `httpx` and curl requests:

- status: `403 Forbidden`
- `Server: cloudflare`
- `Cf-Mitigated: challenge`
- `CF-RAY` identifiers ending in `-DFW`
- title: `Just a moment...`
- visible body text: `Enable JavaScript and cookies to continue`
- no redirect and no `Set-Cookie` response
- the challenge response asks for browser client-hint headers with `Accept-Ch`/`Critical-Ch`
- curl negotiated TLS 1.3 and HTTP/1.1 successfully before receiving the 403
- explicit HTTP/2 also negotiated successfully and received the same 403

The exact IL and missed-game queries both received the same challenge. The response was neither
HTTP 429 nor accompanied by `Retry-After`, so the evidence does not indicate application rate
limiting. A mainstream browser User-Agent alone did not change the result. This rules out malformed
query parameters, missing redirects, HTTP version, and any single ordinary header tested as the
cause. The material distinction is that the interactive browser executed the Cloudflare challenge;
the project intentionally does not reproduce challenge tokens, browser clearance cookies, or
fingerprint data.

## Ordinary HTTP experiments

All requests used the exact observed GET URL and were spaced by at least five seconds; the final
pair used ten seconds. Every row below returned the same managed-challenge 403.

| Experiment | Result |
| --- | --- |
| Plain exact IL GET | 403 Cloudflare challenge |
| Plain exact missed-game GET | 403 Cloudflare challenge |
| Persistent `httpx.Client` with two requests | both 403; no cookies returned |
| Redirect following enabled | 403; redirect chain empty |
| Standard HTML `Accept` | 403 |
| `Accept-Language: en-US,en;q=0.9` | 403 |
| Standard gzip/deflate `Accept-Encoding` | 403 |
| `Referer` set to the site's Search page | 403 |
| Honest project User-Agent | 403 |
| Mainstream Chrome User-Agent alone | 403 |
| Explicit HTTP/1.1 | 403 |
| Explicit HTTP/2 | 403 |
| First GET Search page, then results in the same session | both 403; no cookies returned |
| GET built through `httpx` form parameters in observed order | 403; URL identical |
| Honest User-Agent with ten-second spacing | both 403 |

The reproducible diagnostic is `python legacy/scripts/diagnose_pst_access.py --delay 5`. It redacts cookie
values by design and is not part of the production acquisition path.

## Browser contract and automation experiment

The inspected form is `form#SearchForm`, method `GET`, encoding
`application/x-www-form-urlencoded`, action `SearchResults.php`. A successful results navigation
has Search.php as its referrer and the exact URL constructed by the project.

Stock browser controls, without stealth, proxies, CAPTCHA solving, fingerprint spoofing, or token
extraction, successfully submitted both source types in the current interactive browser session:

| Source | Window | Browser result |
| --- | --- | --- |
| `ILChkBx=yes` | 2026-04-01 through 2026-04-02 | 31 rows across pages of 25 and 6 |
| `InjuriesChkBx=yes` | 2026-04-01 through 2026-04-02 | 13 rows on one page |

The IL Next link was the expected query plus `start=25`. Repeating the missed-game result returned
the same 13 normalized rows, and repeated IL navigation retained the same 25/6 page counts.

This is not accepted as a production solution. A fresh navigation first displayed Cloudflare's
challenge, and the browser referrer showed a redacted `__cf_chl_tk` challenge URL before the normal
page appeared. The browser profile may retain ordinary challenge state, and the available browser
environment is an interactive desktop session rather than a clean scheduler. Full browser request
headers were not available through the safe inspection interface, and browser cookies were
deliberately not inspected. A headless or clean scheduled browser therefore has not been shown to
work reliably, and automating challenge clearance would cross the project's access boundary.

## Environment comparison

| Environment | Result |
| --- | --- |
| Local Codex host on macOS | direct HTTP 403 managed challenge |
| Fresh process in local Docker networking | direct HTTP 403 |
| Independent hosted web fetch used for documentation research | root page fetch returned 403 |
| Interactive desktop browser | works after Cloudflare challenge |
| GitHub Actions or another clean cloud scheduler | not tested; doing so requires deploying or running externally, and no permitted access basis exists yet |

The Docker test was stopped afterward without deleting its PostgreSQL volume. No proxy or alternate
host was used.

## Historical reconciliation

This is a browser-observed comparison, not validation of a production scraper. For 2026-04-01
through 2026-04-02, the existing PostgreSQL history contains 29 IL and 15 missed-game records. The
current site showed 31 IL and 13 missed-game records. Forty-one of 44 current rows matched the
database exactly across date, team, acquired, relinquished, and notes.

The six sides of the three exact-row differences are:

| Date/team/player | Historical row | Current site row |
| --- | --- | --- |
| 2026-04-01 Hornets, P.J. Hall | IL: `placed on IL with ankle injury` | IL: `placed on IL with ankle injury (out for season)` |
| 2026-04-02 Hawks, Jock Landale | missed_game: `ankle injury (out indefinitely)` | IL: `placed on IL with ankle injury (out for season)` |
| 2026-04-02 Pelicans, Karlo Matkovic | missed_game: `back injury (DTD)` | IL: `placed on IL with back injury (out for season)` |

These appear to be later source corrections/reclassifications. They explain the equal total of 44
rows with a two-row shift from missed_game to IL and one changed IL note. Because no production
acquisition path exists, these live observations were not ingested.

## Site-provided access and policy review

The home, basketball, search, and acknowledgements pages expose no API documentation, feed,
download/export control, static dataset, or data license. `sitemap.xml` and common `terms.htm`,
`terms.html`, `privacy.htm`, and `privacy.html` locations returned 404 in the interactive browser.
The only contact mechanism found is `frank@prosportstransactions.com`, linked as “comments welcome”
and for reporting errors.

`robots.txt` could not be reviewed: direct clients received the Cloudflare 403 and the interactive
browser reported `ERR_BLOCKED_BY_CLIENT`. Search did not locate a cached copy. Failure to retrieve
robots.txt is not permission. No Terms page was found, so the available policy neither clearly
permits nor clearly prohibits low-frequency automation. The site's “All rights reserved” notice
also leaves public redistribution/licensing unresolved.

### Draft access request — do not send automatically

Subject: Request for permitted low-frequency NBA transaction data access

> Hello Mr. Marousek,
>
> I maintain a reproducible public NBA injury dataset that attributes and links back to source
> records. May we retrieve the basketball search results for “Movement to/from injured/inactive
> list” and “Missed games due to injury” through a low-frequency automated job, limited to small
> incremental date windows and at least five seconds between requests?
>
> We will not bypass access controls and would follow any rate limits, attribution requirements, and
> redistribution terms you specify. Do you offer a preferred API, export, data feed, or licensed
> access method? Please also let us know whether storing and publicly redistributing normalized
> records derived from the results is permitted.
>
> Thank you.

## Alternative-source feasibility

No candidate is a drop-in replacement for both historical ProSportsTransactions source semantics.

| Candidate | Coverage and fields | Access/reliability | Semantic fit |
| --- | --- | --- | --- |
| [NBA official injury reports](https://official.nba.com/nba-injury-report-2025-26-season/) | Continually updated team/player/status/reason reports for scheduled games; season pages exist, but they are status snapshots rather than the legacy five-field event stream | Official and public; automated reuse/redistribution requires review under NBA terms | Can support current injury status, but not IL movement or exact missed/returned event history |
| [NBA official transactions](https://www.nba.com/players/transactions?TeamID=0) | Dated team transaction narratives with month/team filters | Official and current; NBA terms restrict reuse without permission | Does not provide the missed-game injury stream and does not reproduce the site's injury-list taxonomy |
| [Stats Perform NBA injuries API](https://developer.stats.com/io-docs) | Date-range, season, healed, player, and team injury queries; documentation indicates historical injury support | Authenticated commercial API; licensing and production access required | Closest structured fallback, but exact IL/missed-game event mapping and historical depth need vendor validation |
| [Sportradar NBA injuries API](https://developer.sportradar.com/basketball/reference/nba-daily-injuries) | Daily/active injury data through authenticated endpoints | Mature licensed API requiring an API key | Reliable for current injury status; not evidence of equivalent historical IL and missed-game events |
| [SportsDataIO NBA API](https://sportsdata.io/developers/api-documentation/nba) | Player injury status and per-game data, with some fields documented from 2016 | Authenticated production subscription | Missed games could be inferred, but source notes, explicit return events, and IL semantics would differ |

The official NBA sources also have materially different semantics, and the current NBA Terms state
that public reuse/distribution of basketball content generally requires written permission. A
fallback would therefore require a separately approved product/data decision rather than a scraper
substitution inside Prompt 4.

## Unblocking Prompt 4

One of the following is still required:

1. Written permission plus an ordinary HTTP access path or allowlisting from the site owner.
2. A documented API/export/feed and license that permits this project's storage and redistribution.
3. A clean, realistic scheduler proof that ordinary stock browser automation is reliable, together
   with explicit permission to use it. No challenge-token or cookie workaround is acceptable.
