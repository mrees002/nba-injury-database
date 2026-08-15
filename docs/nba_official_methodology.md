# Official NBA injury-report methodology

Assessment and implementation date: 2026-08-11

This pipeline is additive. The PST-derived `raw_transactions` and `injuries` tables, legacy
processing code, benchmark documentation, and blocked PST scraper remain unchanged for research
and comparison. No destructive migration has been run.

## Archive evidence and access

The NBA says daily injury reports were introduced on 2018-10-18. The current explanatory page is
[`official.nba.com/nba-injury-report-2025-26-season/`](https://official.nba.com/nba-injury-report-2025-26-season/).
Its report-link container was empty during the 2026 offseason and old season-page slugs redirect to
the current page, so it is not a historical index.

Report documents are first-party PDFs at this stable prefix:

```text
https://ak-static.cms.nba.com/referee/injury/Injury-Report_
```

The historical URL inventory is discovered from the Internet Archive CDX URL index. CDX is used
only as evidence of URLs already observed on the official host; PDF bytes are fetched directly
from the NBA. Tracking query strings are removed before URL identity is calculated. The evidence
set through 2026-04-12 contains 12,114 distinct URLs over 1,607 report dates:

| Season | URLs evidenced | Dates represented | Mean versions per represented date |
| --- | ---: | ---: | ---: |
| 2018-19 | 147 | 104 | 1.41 |
| 2019-20 | 449 | 212 | 2.12 |
| 2020-21 | 449 | 199 | 2.26 |
| 2021-22 | 2,054 | 243 | 8.45 |
| 2022-23 | 2,313 | 227 | 10.19 |
| 2023-24 | 1,817 | 224 | 8.11 |
| 2024-25 | 2,238 | 228 | 9.82 |
| 2025-26 through 2026-04-12 | 2,647 | 170 | 15.57 |

Nine evidenced documents exist in December 2018 and 138 more in the remainder of 2018-19, but
that season is visibly sparse. The defensible continuous comparison boundary is 2019-10-22, the
opening date represented for 2019-20. Earlier documents are retained with lineage but must not be
advertised as complete coverage.

The original filename convention is
`Injury-Report_YYYY-MM-DD_HHAM.pdf`/`HHPM.pdf`; the filename omits minutes while the document header
shows `:30`. Starting 2025-12-22, exact-minute variants such as `_05_00PM` and `_01_15PM` also
appear. Hour-only and exact-minute names coexist because half-hour publications keep the older
form. Reports normally appeared one to three times per active date through 2020-21, about eight to
ten times per represented date from 2021-22 through 2024-25, and more frequently after exact-minute
publishing began.

Representative first-party URLs from every requested season returned `200 application/pdf` with
ordinary HTTP. Both Python/httpx and a clean curl Docker container succeeded. No CAPTCHA,
authentication, special cookie, proxy, alternate host, or access-control bypass is used. The
existing PostgreSQL container's BusyBox `wget` could not complete TLS, but a normal curl client in
the same Docker engine did; that is an image-tooling limitation, not an NBA response restriction.

## Observed PDF formats

All inspected documents are text-native PDF 1.4 files. Native word/coordinate extraction works;
OCR would add error and is not used. Six observed table structures are supported:

| Parser format | Observed structure |
| --- | --- |
| `legacy-category-v1` | `Game Date`, `Game Time`, `Matchup`, `Team`, `Player Name`, `Category`, `Reason`, `Current Status`, `Previous Status` |
| `legacy-reason-first-v1c` | transitional November 2019 layout: reason precedes current status and previous status |
| `legacy-current-previous-v1d` | transitional November/December 2019 layout: current status, reason, previous status |
| `legacy-status-history-v1b` | current status/reason and previous status/reason are separate pairs; this coexists with other 2019 layouts |
| `standard-v2` | `Game Date`, `Game Time`, `Matchup`, `Team`, `Player Name`, `Current Status`, `Reason`; category is embedded before ` - ` |
| `compact-v3` | same logical columns as v2, new generator/geometry from 2024 onward; continuation pages repeat the title but can omit the table header |

Game, team, and date cells use visual row spans, so blank cells inherit the current group. Reasons
can wrap across lines and, in the compact generator, may begin above the player baseline. The
parser groups words by coordinates rather than relying on flattened whitespace. It preserves
report text, raw row text, raw player/team/reason values, current status, and the previous
status/reason fields where the source provides them. `NOT YET SUBMITTED` and
`ALL PLAYERS AVAILABLE` are retained as distinct typed, non-player team observations. Unexpected
headers, unknown/misaligned statuses, missing timestamps, invalid PDFs, or unreadable files fail
explicitly.

Parser version `nba-pdf-v5` supersedes `nba-pdf-v4`. The affected source formats are
`legacy-category-v1`, whose team cells can wrap and which contains the observed all-available rows,
and `legacy-status-history-v1b`, whose Minnesota team cell also wraps. Version 5 joins a nearby
team-only continuation line to its pending source row and inherited team context. It also emits an
`all_available` team observation with no player, status, or reason category and with the literal
source assertion retained as `raw_reason` and `raw_row_text`. No classification or episode rule is
changed.

Observed player statuses are `Available`, `Probable`, `Questionable`, `Doubtful`, and `Out`.
Reasons include `Injury/Illness`, illness and health protocols, rest, personal reasons, G League
assignments, suspensions, team/league decisions, rehabilitation/reconditioning, and detailed body
part/type text. Reports contain scheduled game date/time and matchup, but no game ID and no proof
that a listed player actually participated or missed the game.

## Relational schema

The official source is not forced into the legacy transaction table:

- `nba_report_candidates` records every discovered URL, discovery evidence, expected timestamp,
  attempts, terminal/current state, errors, and the resolved report ID after download. Alternate
  URLs with byte-identical content therefore retain relational lineage to one stored document.
- `nba_reports` stores each unique validated PDF once by SHA-256, including bytes, source URL,
  content type/length, timestamps, raw text, parser/format version, and parse state.
- `nba_report_entries` stores each source observation with document/page/row lineage, raw names,
  game fields, current status/reason, optional previous status/reason, and typed entry kind
  (`player`, `not_submitted`, or `all_available`).
- `nba_players` resolves source names to a stable normalized key while retaining every raw name on
  observations. No unsupported official player ID is invented.
- `nba_teams` uses the source's canonical full name and the matchup abbreviation when known.
- `nba_games` represents source-supported scheduled date/time/matchup and away/home team links. No
  unsupported official game ID is invented.
- `nba_injury_conditions` classifies an observation while retaining its raw reason. A condition
  index allows multiple conditions per observation when a future defensible splitter is added.
- `nba_injury_episodes` is the derived event layer with player/team, dates, classification,
  latest status, and methodology version.
- `nba_injury_episode_conditions` links every episode through its classified condition to the exact
  report entry and document. This preserves complete source history without duplicating an
  `InjuryObservation` entity.

PDF bytes live in PostgreSQL, making database backups sufficient to reproduce parsing without
network access. `source_url` and content hash are both unique. A second discovery/backfill run does
not duplicate candidates, documents, or observations; alternate URLs with identical content are
retained as candidates pointing by diagnostic text to the existing content identity.

## Classification v7

The derived NBA classifier is versioned as `nba-reason-v7`. It retains the raw reason unchanged and
derives medical versus explicit non-medical semantics, normalized anatomy, condition type,
laterality, and conservatively separable compound conditions. Anatomy and non-injury matching use
token/phrase boundaries. Lexical slashes such as `N/A`, `Injury/Illness`, and `tib/fib` are not
condition delimiters. Mixed rows retain both a fully stated medical condition and a structurally
separate G League, rest, personal, or other explicit non-medical clause.

Unknown anatomy or type stays null; reconstruction is a procedure rather than an inferred tear;
recovery language takes precedence over a historical procedure; and every derived condition links
to the same unchanged source entry. The complete taxonomy, rules, archive-wide measurements,
residual ambiguity, and offline rebuild instructions are documented in
[`nba_classification_methodology.md`](nba_classification_methodology.md).

## Episode methodology v3

The derived episode methodology is versioned as `nba-episodes-v3`. It processes classified
conditions in deterministic report-publication order and uses player, team, anatomy, laterality,
named structure, compatible condition wording, source-row identity, explicit availability, and
bounded observation gaps to continue or split episodes. Separate conditions from one source row
remain separate, while all supporting conditions retain relational lineage through their raw
report entries and stored reports.

Episode dates are observation dates, not medically established onset or recovery dates. Absence
from a report does not imply recovery, and team changes intentionally split episodes. The complete
rules, final full-archive audit, residual limitations, and offline rebuild instructions are
documented in [`nba_episode_methodology.md`](nba_episode_methodology.md).

NBA quality and benchmark reports use an NBA-specific season label. The 2019-20 season remains
active through the 2020-10-11 Finals; this tested exception avoids assigning October 2020 bubble
reports to 2020-21. The shared PST processing helper is not changed, preserving its characterized
legacy rebuild behavior.

## Games missed and participation

An `Out` report is a pregame status, not participation evidence. Actual games missed must not be
inferred from it. Supporting that query requires an official NBA schedule/box-score or player game
log source with stable game and player IDs. The clean extension is a `game_participations` table
linked to `nba_games` and `nba_players`; a missed game is then a scheduled rostered game with no
participation, interpreted alongside the episode's report observations. That dependency is not
implemented in this migration.

## Operational behavior

The NBA client has a configurable descriptive User-Agent, timeout, request interval, bounded
exponential-backoff retries, strict `application/pdf` validation, and `%PDF-` signature validation.
HTTP 404, transient HTTP failure, invalid PDF, and parse failure are separate candidate states.
Each report commits independently, so interruption loses at most the active report. Saved PDFs can
be reparsed after a parser-version change with no network request. Candidate rows are locked before
acquisition so concurrent workers skip a report already owned by another worker, and a stored,
current-parser report repairs a stale candidate status without downloading again. A PostgreSQL
advisory lock prevents two CLI acquisition jobs from multiplying the configured request rate. The
unattended default is one request per second. Unit/integration tests generate
small text-native PDF fixtures for every observed structure and do not require live access.

The old PST pipeline and scraper remain available only for benchmark/audit use. A later explicit
decision is required before removing any PST-derived production data.
