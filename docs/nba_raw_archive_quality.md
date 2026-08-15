# NBA raw archive quality audit

Audit date: 2026-08-12

Scope: the official-NBA candidate, report, report-entry, source-entity, and source-lineage layers in
PostgreSQL after the complete offline `nba-pdf-v5` reparse. Classification, injury conditions,
InjuryEpisodes, deduplication, PST comparison, API, frontend, deployment, and scheduling were not
evaluated or changed.

PostgreSQL was treated as authoritative. This certification did not download reports or retry
acquisition failures.

## Decision

### RAW INGESTION: PASS WITH DOCUMENTED GAPS

The stored raw archive is internally consistent and reproducible. All 12,112 reports were reparsed
from their PostgreSQL-stored PDF bytes with `nba-pdf-v5`; all completed successfully. The two known
legacy parser defects are absent from the final structured rows, and no new structural or lineage
failure was found.

The only unresolved registered candidates are two bounded HTTP failures on 2023-07-13. They are
documented acquisition gaps, not parser failures. Twenty other versions from that date are present.

## Final acquisition snapshot

| Measure | Value |
| --- | ---: |
| candidates | 12,114 |
| parsed candidates | 12,112 |
| `http_failed` candidates | 2 |
| stored reports | 12,112 |
| parsed `nba-pdf-v5` reports | 12,112 |
| stored report entries | 1,127,319 |
| stored PDF bytes | 693,161,878 |
| unique report dates | 1,607 |
| earliest stored report | 2018-12-20 |
| latest stored report | 2026-04-12 |
| reports without PDF bytes | 0 |
| reports whose stored byte length is wrong | 0 |
| reports without entries | 0 |
| reports not parsed on `nba-pdf-v5` | 0 |
| reports without extracted raw text | 0 |

Candidate status vocabulary is valid and the final counts are exactly `parsed=12,112` and
`http_failed=2`. There are no `discovered`, `downloaded`, `missing`, `invalid_pdf`, or
`parse_failed` candidates. Report parse status is `parsed` for every stored report.

## Documented acquisition gaps

| Candidate | Expected report | Status | Candidate attempts | Per-attempt HTTP tries | Stored report | Source URL |
| ---: | --- | --- | ---: | ---: | --- | --- |
| 5391 | 2023-07-13 00:30 | `http_failed` | 2 | 4 | none | `https://ak-static.cms.nba.com/referee/injury/Injury-Report_2023-07-13_12AM.pdf` |
| 5403 | 2023-07-13 12:30 | `http_failed` | 2 | 4 | none | `https://ak-static.cms.nba.com/referee/injury/Injury-Report_2023-07-13_12PM.pdf` |

The stored errors say each download failed after the client's four bounded attempts. No PDF bytes
or report rows exist for either candidate, and neither was retried during this audit. The same date
has 20 successfully stored and parsed versions at 01:30 through 11:30 and 13:30 through 21:30.
There is no other unresolved candidate.

The historical inventory itself has source-coverage limits that are separate from these two known
candidate failures: the sparse 2018-19 segment begins on 2018-12-20, so 2019-10-22 remains the
defensible continuous comparison boundary. Calendar gaps corresponding to the COVID-19 shutdown,
offseasons, All-Star breaks, and days without postseason games are not parser failures. The fixed
historical endpoint of 2026-04-12 is present.

## Parser and structured-source integrity

| Measure | Result |
| --- | ---: |
| entries | 1,127,319 |
| player entries | 1,007,541 |
| `not_submitted` entries | 119,754 |
| `all_available` entries | 24 |
| minimum / median / mean / p90 / p99 / maximum entries per report | 1 / 97 / 93.07 / 150 / 185 / 230 |
| reports with duplicate or gapped row numbers | 0 |
| rows without valid page/row/raw-row lineage | 0 |
| player rows without raw player name, player entity, status, or reason | 0 |
| rows without raw team, team entity, game entity, date, time, or matchup | 0 |
| entry/game date or matchup mismatches | 0 |
| entry teams outside known game participants | 0 |
| malformed entry types or status values | 0 |
| malformed typed non-player observations | 0 |

Player status vocabulary is exactly `Available`, `Probable`, `Questionable`, `Doubtful`, and
`Out`. Previous status is null or one of those values or the source placeholder `-`. Non-player
observations have no player relationship and no current status. Unknown or misaligned player
statuses are rejected by the parser rather than silently stored.

### Parser-v5 defect repair certification

- `Minnesota` / `Timberwolves` fragment entries: **0**.
- `Minnesota` / `Timberwolves` fragment team entities: **0**.
- Known-game team mismatches: **0**.
- Independent `all_available` observations: **24**, matching all 24 phrases in stored report text.
- Malformed `all_available` observations: **0**.
- `ALL PLAYERS AVAILABLE` attached to another entry type: **0**.

The wrapped `Minnesota Timberwolves` source cells now retain the complete team on the affected row
and inherited neighboring rows. `ALL PLAYERS AVAILABLE` is stored as an independent team-level
observation with no player, status, or reason category; the literal source assertion remains in
`raw_reason` and `raw_row_text`.

## Supported report formats

No stored report has a null, unknown, mixed, or fallback format.

| Format | Date range | Reports | Source structure |
| --- | --- | ---: | --- |
| `legacy-category-v1` | 2018-12-20 to 2019-11-14 | 215 | Category and Reason followed by Current and Previous Status; supports wrapped team cells and typed all-available rows. |
| `legacy-reason-first-v1c` | 2019-11-15 to 2019-11-19 | 12 | Reason before Current and Previous Status. |
| `legacy-current-previous-v1d` | 2019-11-20 to 2019-12-16 | 48 | Current Status, Reason, and Previous Status. |
| `legacy-status-history-v1b` | 2019-12-17 | 2 | Separate current and previous status/reason pairs; supports wrapped team cells. |
| `standard-v2` | 2019-12-18 to 2023-05-02 | 4,928 | Current Status and Reason with repeated headers and multiline reasons. |
| `compact-v3` | 2023-05-02 to 2026-04-12 | 6,907 | Compact geometry; continuation pages can omit the repeated table header. |

## Source preservation and lineage

Every report retains its official source URL, candidate relationship, SHA-256 hash, PDF bytes,
content type and length, download/parse timestamps, embedded report date/time, complete extracted
raw text, parser/format versions, and parse state. Every entry retains its report, page, contiguous
report-local row number, raw row text, source game fields, raw team/player names, status/reason
fields, typed entry kind, and source-entity relationships.

All 12,112 parsed candidates resolve to the correct stored report. There are no missing or broken
candidate/report relationships, source URL mismatches, lineage-date mismatches, or unresolved
candidates other than 5391 and 5403. Candidate, report, and entry source-lineage fields are all
populated. The original discovery label remains `/private/tmp/nba_report_manifest.txt`; ongoing
operation and offline reproduction do not depend on that path because the full candidate URL set,
resolved-report links, source URLs, hashes, and PDF bytes are durable in PostgreSQL.

A fresh SHA-256 audit read all 693,161,878 stored bytes and recomputed all 12,112 hashes. There were
zero mismatches. Every stored object also has a valid `%PDF-` signature and agrees with its recorded
byte length.

PostgreSQL contains 1,254 player entities, 31 team entities, and 9,863 game entities. Every player
and team entity is referenced. The team set is the 30 NBA franchises plus the source-supported
`Non-NBA Team`; the two parser-created Minnesota fragments no longer exist. Eight source games have
an `UNK` opponent and consequently lack one or both participant relationships. Those explicit
source placeholders are the only expected incomplete game participants; every report entry still
has a valid game and team relationship.

## Downstream methodology status

This audit certifies raw acquisition, document preservation, parsing, relational source integrity,
and lineage only. It does not certify classification accuracy, condition normalization,
InjuryEpisode methodology, deduplication, PST comparison, inferred games missed, or any product
layer. Those downstream questions remain unevaluated and must not be inferred from this Stage A
decision.

## Verification performed

- Complete offline reparse: selected 12,112; parsed 12,112; failed 0.
- Stored-PDF SHA-256 audit: 12,112 checked; 0 mismatches; 693,161,878 bytes checked.
- Focused raw ingestion/parser tests: 27 passed. No benchmark suite was run.
- Ruff lint: passed.
- Ruff format check: passed; 83 files already formatted.
- Alembic current/head: `0005_all_available_entry_type`.
- Alembic schema drift: no new upgrade operations detected.
