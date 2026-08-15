# NBA injury database handoff

Snapshot generated 2026-08-12. The repository and PostgreSQL database are authoritative; verify
this snapshot against both before acting.

## 1. Project goal

Build an NBA injury database and public website using official NBA injury-report PDFs as the
production source:

`Official NBA injury reports -> discover PDFs -> download/validate -> parse raw report observations -> PostgreSQL -> derived classifications -> distinct InjuryEpisodes -> API -> public website/downloads -> automatic incremental updates`

ProSportsTransactions (PST) is retained only as a historical benchmark and regression source. It
is not the intended production source.

## 2. Current development strategy

### Stage A: raw ingestion — current priority

Discover official NBA PDFs, download and validate them, parse faithful report observations, and
store relational source data in PostgreSQL. Preserve URLs, PDF bytes, hashes, extracted text, raw
row text, source fields, and page/row lineage so all downstream work can be rerun offline.

### Stage B: methodology — wait for Stage A

After the raw historical archive is complete, evaluate classification, body-part and injury-type
normalization, laterality, compound conditions, InjuryEpisode construction,
recurrence/deduplication, and PST overlap.

### Stage C: product — deferred

After methodology is accepted, build the incremental NBA updater with `UpdateRun` auditing, API,
website/downloads, and scheduler/deployment.

Do not mix Stage A acquisition fixes with Stage B tuning unless raw-data correctness requires it.

## 3. Current repository status

- Branch: `main`
- Commit: `06e467afa1a3657b9cd10704ac7b9333cd82b215`
- Working tree: **dirty by design**; the NBA implementation and cleanup have not been committed.
- No backfill process is running.
- No API, frontend, scheduler, or production incremental updater exists yet.

Important uncommitted work includes migrations `0002`–`0005`; the NBA models, discovery, client,
parser, persistence, classifier, episode builder, quality/audit/benchmark code, CLI jobs, fixtures,
and tests; configuration/dependency changes; methodology documentation; and the conservative
legacy-document cleanup. Preserve all of it. Root retired prompts/specifications and PST documents
appear as deletions because their replacements are untracked under `legacy/`.

Active structure:

- `app/nba/`: official-NBA source and provisional derived methodology code.
- `app/db/`, `app/models/`, `app/jobs/`: shared SQL infrastructure, models, and entry points.
- `alembic/`: all schema migrations.
- `tests/nba/` and `tests/fixtures/nba_reports/`: active offline NBA tests and generated PDF inputs.
- `docs/`: current methodology and cleanup decisions.
- `legacy/`: original PST pipeline, archived specs/docs, and historical references.
- `app/processing/`, `app/services/csv_import.py`, `app/scraper/`, and corresponding tests remain at
  stable import paths for PST reproducibility; they are legacy/benchmark code.

Cleanup moved historical documents under `legacy/`, retained stable Python paths, and removed only
generated caches. See `docs/repository_cleanup.md` and `legacy/README.md`. No source, migration,
test, database row, PDF, Docker volume, or uncommitted work was deleted.

## 4. Current database state

PostgreSQL 16 is running and healthy in Docker Compose.

**Critical schema state:** PostgreSQL and the repository are both at
`0005_all_available_entry_type`. Alembic reports no schema drift. Migration `0004` added and
backfilled durable candidate/report lineage; migration `0005` allows the parser's typed
`all_available` team observation. The schema is ready for the user-run offline reparse.

| Item | Current value |
| --- | ---: |
| `raw_transactions` | 44,126 |
| `injuries` (legacy PST-derived) | 12,877 |
| `nba_report_candidates` | 12,114 |
| `nba_reports` | 12,112 |
| `nba_report_entries` | 1,127,295 |
| `nba_injury_conditions` | 1,009,476 |
| `nba_injury_episodes` | 0 |
| earliest stored NBA report | 2018-12-20 |
| latest stored NBA report | 2026-04-12 |
| stored PDF bytes | 693,161,878 |

Candidate states:

| Status | Count | Interpretation |
| --- | ---: | --- |
| `parsed` | 12,112 | Candidate marked complete with durable report lineage |
| `http_failed` | 2 | Genuine exhausted-download failures for 2023-07-13 |

There are no unattempted candidates and no stale `parse_failed` labels. The two HTTP failures are
the bounded 2023-07-13 source gaps documented in `docs/nba_raw_archive_quality.md`; do not retry
them as part of the offline reparse.

All 12,112 reports have stored PDF bytes, `parse_status=parsed`, and database parser version
`nba-pdf-v4`. Repository parser `nba-pdf-v5` is prepared but the full archive has intentionally not
been reparsed yet. Stored report formats are:
Stored report formats are:

| Format | Reports |
| --- | ---: |
| `standard-v2` | 4,928 |
| `compact-v3` | 6,907 |
| `legacy-category-v1` | 215 |
| `legacy-current-previous-v1d` | 48 |
| `legacy-reason-first-v1c` | 12 |
| `legacy-status-history-v1b` | 2 |

The condition layer is provisional and mixed (`nba-reason-v4` 657,836;
`nba-reason-v6` 351,640). Do not tune or separately synchronize classification during Stage A.
The episode table is empty.

The PST benchmark is intact: 28,345 IL plus 15,781 missed-game raw rows, 12,877 normalized legacy
injuries, 1,470 players, and injury dates 2010-10-03 through 2026-04-12.

## 5. NBA archive and discovery state

- Official PDF host/prefix:
  `https://ak-static.cms.nba.com/referee/injury/Injury-Report_`
- Discovery reads official-URL evidence from the Internet Archive CDX index, then downloads bytes
  directly from the NBA host. CDX is not the document source.
- Known inventory: 12,114 URL candidates from 2018-12-20 through the fixed historical endpoint
  2026-04-12. Archive coverage is sparse in 2018-19; 2019-10-22 is the defensible continuous
  comparison boundary.
- Current stored checkpoint: 12,112 parsed reports through 2026-04-12; only the two bounded HTTP
  failures are unresolved.
- Remaining work for this handoff is offline parsing only; do not download more reports.
- Query strings are removed before URL identity. Candidate `source_url` is unique; report
  `content_hash` and report `source_url` are unique.
- Byte-identical alternate URLs retain separate candidate lineage but resolve to one report.
  Migration `0004` is applied and every parsed candidate has durable `resolved_report_id` lineage.
- PDF bytes are stored in PostgreSQL. All current reports can be reparsed offline.
- Reports are text-native PDF 1.4 documents; OCR is generally unnecessary and is not used.

## 6. Parser state

- Repository parser: `nba-pdf-v5` in `app/nba/parser.py`; all stored reports remain on
  `nba-pdf-v4` until the user runs the full offline reparse.
- Supported layouts: the six format versions listed in section 4.
- Parsing uses native PDF word coordinates and visually inherited date/game/team cells.
- Wrapped/multiline reasons are reassembled; compact-layout fragments that start above a player
  baseline are assigned by coordinate proximity.
- Compact continuation pages may repeat the title while omitting the table header; the previous
  header geometry is reused.
- Current status is preserved. Legacy layouts also retain previous status and, where present,
  previous reason. Standard/compact layouts do not invent history fields.
- Every entry retains report, page, row, raw team/player/reason, status/history fields, and
  `raw_row_text`. Each report retains source URL, bytes, hash, full extracted text, content metadata,
  parser/format versions, and errors.
- `NOT YET SUBMITTED` and `ALL PLAYERS AVAILABLE` are preserved as distinct typed non-player team
  observations.
- Unknown/misaligned statuses, unexpected or mixed headers, missing timestamps, invalid PDFs, and
  unreadable files fail explicitly. A future NBA layout requires a new fixture/parser change.
- Reports provide scheduled games and pregame status snapshots, not official game IDs or proof of
  participation/missed games.

Version 5 fixes two characterized legacy defects: a nearby team-only continuation is joined to the
pending `Minnesota Timberwolves` row/context, and `ALL PLAYERS AVAILABLE` becomes an independent
`all_available` entry rather than contaminating a neighboring player or `not_submitted` row. The
version change affects `legacy-category-v1` and `legacy-status-history-v1b`; no classification or
InjuryEpisode rule changed. Focused tests and a 10-report stored-PDF sample pass. The sample covered
all six generations; because both status-history reports contain Minnesota wrapping, unaffected
rows inside report 277 were used for that generation.

**The full 12,112-report reparse has intentionally not been run by Codex.**

## 7. Data model

| Model/table | Layer | Purpose |
| --- | --- | --- |
| `NBAPlayer` / `nba_players` | RAW / SOURCE reference | Stable normalized name key; raw names stay on entries |
| `NBATeam` / `nba_teams` | RAW / SOURCE reference | Source team name and known abbreviation |
| `NBAGame` / `nba_games` | RAW / SOURCE reference | Source-supported scheduled date/time/matchup; no invented NBA ID |
| `NBAReportCandidate` / `nba_report_candidates` | RAW / SOURCE | Discovered URL, evidence, expected timestamp, attempts, state/errors, resolved report lineage |
| `NBAReport` / `nba_reports` | RAW / SOURCE | Unique validated PDF bytes/hash, URL, raw text, parse metadata |
| `NBAReportEntry` / `nba_report_entries` | RAW / SOURCE | Faithful source observation with document/page/row lineage |
| `NBAInjuryCondition` / `nba_injury_conditions` | DERIVED / METHODOLOGY | Provisional classified condition(s) linked to one raw entry |
| `NBAInjuryEpisode` / `nba_injury_episodes` | DERIVED / METHODOLOGY | Provisional distinct injury event |
| `NBAInjuryEpisodeCondition` / `nba_injury_episode_conditions` | DERIVED / METHODOLOGY | Episode-to-condition/source lineage |
| `RawTransaction` / `raw_transactions` | RAW / SOURCE (legacy PST) | Canonical idempotent PST source record |
| `Injury` / `injuries` | DERIVED / METHODOLOGY (legacy PST) | Legacy normalized/deduplicated PST injury |
| `UpdateRun` / `update_runs` | RAW / SOURCE operational audit | Generic run accounting; not yet wired to the NBA updater |

## 8. Important commands

Run from the repository root with the existing `.venv`.

Start PostgreSQL and inspect migrations:

```bash
docker compose up -d db
.venv/bin/alembic current
.venv/bin/alembic heads
.venv/bin/alembic check
```

Verify the already-applied parser-support migration:

```bash
.venv/bin/alembic current
.venv/bin/alembic check
```

Do not resume acquisition for this task. The remaining operation is the user-run offline reparse
shown below.

Monitor reparse progress with local Compose defaults:

```bash
docker compose exec -T db psql -U nba -d nba_injuries -c \
  "SELECT count(*) FILTER (WHERE parser_version = 'nba-pdf-v5' AND parse_status = 'parsed') AS completed, count(*) FILTER (WHERE parser_version IS DISTINCT FROM 'nba-pdf-v5' OR parse_status <> 'parsed') AS remaining, count(*) AS total FROM nba_reports WHERE report_date BETWEEN DATE '2018-12-20' AND DATE '2026-04-12';"
```

Inspect failures:

```bash
docker compose exec -T db psql -U nba -d nba_injuries -c \
  "SELECT r.id, r.report_date, r.report_time, r.parse_status, r.parser_version, left(r.parse_error, 300) AS error, r.source_url FROM nba_reports r WHERE r.parse_status = 'failed' OR r.parser_version IS NULL ORDER BY r.report_date, r.report_time;"
```

Stop a foreground reparse with Ctrl-C. Prior report commits remain durable; rerun the same command
to resume.

Reparse saved reports offline after a parser upgrade:

```bash
.venv/bin/python -m app.jobs.reparse_nba_reports \
  --start-date 2018-12-20 --end-date 2026-04-12
```

Checks:

```bash
.venv/bin/pytest tests/nba -q
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/alembic check
```

No required workflow depends on `/private/tmp`, a Codex attachment, or a temporary manifest. The
registered inventory in PostgreSQL is sufficient for resume.

## 9. Resumability and idempotency

- Discovery is idempotent by normalized source URL.
- Normal runs select only `discovered`; `--retry-failures` additionally selects `http_failed`,
  `invalid_pdf`, and `parse_failed`. `missing` is terminal unless changed deliberately.
- Current-parser reports with entries are skipped. A stale candidate state backed by a parsed
  current-parser report is reconciled to `parsed` without downloading.
- Each report is committed independently. Ctrl-C rolls back at most the active transaction; the
  job’s `finally` block releases its PostgreSQL advisory lock.
- Candidate rows use `FOR UPDATE SKIP LOCKED`; a process-wide PostgreSQL advisory lock prevents two
  CLI acquisition jobs from multiplying the configured request rate.
- Attempts transition through `discovered -> downloaded -> parsed`, or to explicit `missing`,
  `http_failed`, `invalid_pdf`, or `parse_failed` states.
- Report URL and SHA-256 uniqueness prevent duplicate documents. Byte-identical alternate URLs
  remain candidates and point to one report through `resolved_report_id` after migration `0004`.
- Reparse uses stored bytes, replaces entries/conditions only for stale or failed parser versions,
  and refuses to run while episodes exist because reparsing would invalidate them. Episodes are
  currently empty. The reparse CLI shares the acquisition advisory lock, so a second reparse or
  backfill cannot mutate the raw report layer concurrently. When it completes, it removes only
  unreferenced team resolver rows left behind by older parser output.

Previously discovered bug: after an interruption committed an `NBAReport` but left its candidate
state stale, a retry attempted a second report insert and hit the unique candidate constraint. The
current code first finds an existing candidate report, repairs state, and has an integration test.
The prior 185 stale `parse_failed` labels have been reconciled. Migration `0004` is applied and all
12,112 parsed candidates have report lineage.

During cleanup, an inherited acquisition child continued briefly after its wrapper was interrupted
and advanced the checkpoint before exiting. Counts then remained stable, and no backfill process
is currently running.

## 10. Classification status — PROVISIONAL

- Current code version: `nba-reason-v6` in `app/nba/classification.py`.
- Current stored data is mixed provisional output: 657,836 `nba-reason-v4` rows and 351,640
  `nba-reason-v6` rows.
- Rule-based support includes body part, injury type, left/right/bilateral laterality, explicit
  non-injury reasons, unclassified injury retention, and conservative splitting of independently
  stated compound conditions.
- Implemented fixes include term-boundary matching, specific ligament/anatomy precedence,
  official-report vocabulary, explicit non-injury precedence, laterality preservation, and shared
  source lineage for compound conditions.
- It remains lexical and incomplete; body-part/type normalization is not accepted methodology.
  Do not reclassify or tune it until raw archive acquisition is complete.

## 11. InjuryEpisode status — PROVISIONAL

- Current code methodology: `nba-episodes-v2` in `app/nba/episodes.py`.
- Database state: zero episodes and zero episode-condition links.
- Inputs are injury-classified conditions joined to raw entries/reports, ordered by player,
  scheduled game date, publication timestamp, and row identity.
- Tested rules include 14-day exact-reason merging, seven-day same-body merging, three-day
  body-null/same-type merging, laterality conflicts, deterministic ties, explicit `Available`
  closure, same-day status supersession, unclassified conditions, and idempotent rebuilds.
- Conditions split from one raw entry remain separate episodes initially and can be extended by
  later observations independently.
- Weaknesses: gap windows are methodological heuristics; disappearance is not a recovery signal;
  `Available` is not a medical recovery date; scheduled status does not establish participation;
  and recurrence boundaries are not definitive.

Episode methodology is not final and must not be tuned further until the raw historical NBA
archive is complete.

## 12. PST benchmark status

PST is retained for historical benchmarking, regression, and NBA/PST overlap comparison. It is not
ground truth and its transaction/missed-game semantics differ from NBA pregame status snapshots.

Known legacy weaknesses include discarded laterality during deduplication, fixed 30/90/180/365/750
day merge windows, dropping some missing-body-part observations, unstable equal-score pandas sort
ties, synthetic source preferences, and no definitive injury-episode semantics. The characterized
SQL rebuild intentionally preserves one tied-row substitution described in
`legacy/docs/legacy_regression.md`; do not silently change it.

## 13. Expensive-to-rediscover findings

- Official PDF host: `ak-static.cms.nba.com/referee/injury/`.
- Earliest evidenced URL: 2018-12-20; known inventory: approximately 12.1k URLs through 2026-04-12.
- Original filenames omit minutes and imply `:30`; exact-minute variants such as `_05_00PM` begin
  2025-12-22 and coexist with older names.
- Six historical layout generations are supported; documents are text-native and do not need OCR.
- Ordinary HTTP with a descriptive, rate-limited client works for NBA PDFs; no authentication,
  CAPTCHA, cookie, or access-control bypass is used.
- Direct PST scraping is Cloudflare-blocked in the assessed environment; its offline fixture parser
  remains benchmark-only.
- NBA reports are repeated status snapshots. PST rows are transactions or missed-game events; the
  sources are conceptually different and should not be forced into one raw table.

## 14. Important file map

| Path | Purpose |
| --- | --- |
| `app/nba/discovery.py` | CDX query, URL parsing/normalization, manifest parser |
| `app/nba/client.py` | Rate-limited/retrying official-PDF HTTP client and validation |
| `app/nba/parser.py` | `nba-pdf-v4` coordinate-based raw parser |
| `app/nba/repository.py` | Candidate registration, entity resolution, raw entry persistence |
| `app/nba/backfill.py` | Resumable candidate processing and state transitions |
| `app/jobs/backfill_nba_reports.py` | Historical discovery/backfill CLI and advisory lock |
| `app/nba/reparse.py`, `app/jobs/reparse_nba_reports.py` | Offline parser-version rebuild |
| `app/models/nba.py` | Official NBA relational models |
| `app/nba/classification.py` | Provisional `nba-reason-v6` classifier |
| `app/nba/episodes.py` | Provisional `nba-episodes-v2` methodology |
| `app/nba/quality.py`, `app/nba/audit.py` | Data-quality and classification audits |
| `app/nba/benchmark.py` | NBA episode versus PST comparison |
| `app/jobs/reclassify_nba_conditions.py` | Derived reclassification job; defer during Stage A |
| `app/jobs/rebuild_nba_episodes.py` | Derived episode rebuild; defer during Stage A |
| `alembic/versions/0002_nba_official_reports.py` | Adds official-NBA schema |
| `alembic/versions/0003_report_previous_status.py` | Adds previous reason/status source fields |
| `alembic/versions/0004_candidate_report_lineage.py` | Pending resolved-report lineage migration |
| `tests/nba/` | 46 offline NBA unit/integration tests |
| `tests/fixtures/nba_reports/` | Small source-layout fixtures used to generate text-native PDFs |
| `docs/nba_official_methodology.md` | Architecture, archive evidence, parser and provisional methodology |
| `docs/repository_cleanup.md` | Active/legacy cleanup inventory and database checkpoint |
| `legacy/process_injuries_pipeline.py` | Original PST behavioral reference |
| `legacy/README.md` | Map of retained PST benchmark code and documents |
| `legacy/docs/legacy_regression.md` | Full PST SQL-versus-legacy regression result |

## 15. Test and quality state

Verified 2026-08-12:

- NBA tests: **46 passed**.
- Full pytest: **131 passed**.
- Ruff lint: **passed**.
- Ruff formatting: **79 files already formatted**.
- `git diff --check`: **passed**.
- PostgreSQL/Docker: **healthy**.
- PST benchmark integrity: **44,126 raw / 12,877 injuries**, intact.
- Alembic drift/state check: **not passing** because database `0003` is behind repository head
  `0004`; this was intentionally not mutated during handoff.

# NEXT TASK

Prepare and verify a user-runnable local historical NBA raw backfill.

The user wants the long network-bound acquisition/parsing process to run directly from Terminal
without Codex supervising it.

The next fresh Codex conversation should:

1. read `HANDOFF.md`;
2. inspect `handoff_state.json`;
3. verify both against the current repository and PostgreSQL state;
4. make only the minimum changes required for a clean user-run historical backfill, beginning with
   review/application of pending migration `0004_candidate_report_lineage`;
5. provide commands to start, monitor, stop, inspect failures, and resume;
6. test those commands only on a small bounded sample if needed;
7. **not** run the full remaining historical archive itself;
8. **not** tune classification;
9. **not** tune InjuryEpisode logic; and
10. **not** benchmark PST yet.

After the user finishes the historical raw backfill locally, the next phase will analyze the
completed raw SQL dataset.
