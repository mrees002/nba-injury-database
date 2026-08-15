# NBA Injury Database

The active production direction uses official NBA injury-report PDFs as the authoritative future
source. The intended flow is:

`NBA PDFs -> discovery/download -> raw parsing -> PostgreSQL -> derived classification -> InjuryEpisode methodology -> API -> website -> scheduled incremental updates`

The Python/PostgreSQL foundation, NBA relational model, discovery/downloader/parser, raw
persistence, and offline tests are implemented. Complete and validate raw NBA archive ingestion
before further classification, episode, deduplication, or PST benchmark tuning. Incremental
updates, API, and UI remain deferred.

ProSportsTransactions (PST) data and code remain intact only for historical comparison,
regression, and methodology validation. PST is not the intended production ingestion source, and
no destructive PST-to-NBA migration has been executed. See [`legacy/README.md`](legacy/README.md)
for the retained benchmark inventory and `legacy/specifications/` for retired starter prompts.
The conservative repository reorganization is recorded in
[`docs/repository_cleanup.md`](docs/repository_cleanup.md).

## Prerequisites

- Python 3.12 or newer
- Docker with Docker Compose

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
```

Start PostgreSQL and apply all available migrations:

```bash
docker compose up -d db
alembic upgrade head
```

This creates the legacy benchmark tables plus the official-NBA report, observation,
classification, and episode tables and their indexes and constraints.

Stop the local database with:

```bash
docker compose down
```

Use `docker compose down --volumes` only when you deliberately want to delete the local
PostgreSQL data volume.

## Checks

```bash
pytest
ruff check .
ruff format --check .
```

The tests execute `legacy/process_injuries_pipeline.py` directly. They characterize its current
classification, season assignment, exact-duplicate scoring, recovery filtering, and time-window
deduplication behavior without porting or changing those rules.

## Historical CSV import

Apply the migration first, then import each historical file with its source type stated explicitly:

```bash
python -m app.jobs.import_csv --source-type il path/to/NBA_IL.csv
python -m app.jobs.import_csv --source-type missed_game path/to/NBA_Missed_Games.csv
```

The importer requires the legacy columns `Date`, `Team`, `Acquired`, `Relinquished`, and `Notes`.
It accepts dates in `YYYY-MM-DD`, `MM/DD/YYYY`, or `MM/DD/YY` form, preserves the four text fields
exactly as read, and prints `read`, `inserted`, `skipped`, and `invalid` counts. A deterministic hash
of the source type, normalized date, and source text enforces idempotency, so importing the same
rows again inserts zero duplicates. Historical files do not contain source URLs, so their
`source_url` value remains null rather than being invented.

## Rebuild normalized injuries

After raw transactions have been imported, rebuild the normalized `injuries` table with:

```bash
python -m app.jobs.rebuild_injuries
```

The rebuild applies the characterized legacy classification and deduplication rules, replaces the
normalized table in one transaction, and retains the selected `RawTransaction` ID and source type
on every resulting injury. Repeating the command produces the same normalized injury content.
The full historical comparison and its one known tied-row difference are documented in
[`legacy/docs/legacy_regression.md`](legacy/docs/legacy_regression.md).

## Official NBA injury reports

Historical report discovery, downloading, validation, parsing, and persistence use one resumable
command. Run it only for an explicitly approved acquisition task; repository cleanup and analysis
must not implicitly resume the archive backfill:

```bash
python -m app.jobs.backfill_nba_reports \
  --start-date 2018-12-20 \
  --end-date 2026-04-12
```

The default discovery adapter queries the Internet Archive CDX URL index for evidence of official
PDF URLs, then downloads every document from the NBA's `ak-static.cms.nba.com` host. A saved,
newline-delimited URL manifest can be supplied with `--manifest`; `--discover-only`, `--limit`, and
`--retry-failures` support inspection and checkpointed recovery. Once discovery is registered,
`--registered-only` resumes entirely from PostgreSQL without querying the archive index again.
The default client interval is one request per second. PostgreSQL stores the PDF bytes,
SHA-256 hashes, raw extracted text, original row text, source URLs, parser versions, and explicit
failure states. Repeated runs process only unresolved candidates.

When parser behavior changes, reparse stored bytes without network access:

```bash
.venv/bin/python -m app.jobs.reparse_nba_reports \
  --start-date 2018-12-20 \
  --end-date 2026-04-12
python -m app.jobs.reclassify_nba_conditions
python -m app.jobs.audit_nba_classification
```

The reparse job selects only reports not already stored with the current parser version, reads PDF
bytes from PostgreSQL, and commits each report independently. Repeating the same command resumes
after the last committed report without contacting the NBA site. At the end it removes only team
resolver rows that have no report-entry or game references, so obsolete parser-created fragments do
not remain as false source entities. It shares the acquisition advisory lock, preventing concurrent
backfill and reparse processes from mutating the same raw reports.

After raw-archive validation, rebuild the derived episode layer and print data-quality metrics:

```bash
python -m app.jobs.rebuild_nba_episodes
python -m app.jobs.report_nba_quality
python -m app.jobs.benchmark_nba_vs_pst
```

The archive evidence, observed PDF formats, schema rationale, episode methodology, benchmark
limits, and migration decision are documented in
[`docs/nba_official_methodology.md`](docs/nba_official_methodology.md). The benchmark derives the
continuous overlap period and writes local review artifacts under `artifacts/validation/`; its
matching and output contract are documented in
[`docs/nba_vs_pst_validation.md`](docs/nba_vs_pst_validation.md). Unit and integration tests use
generated text-native PDF fixtures and never require live network access.

## Legacy ProSportsTransactions scraper (benchmark only)

The programmatic entry point accepts a source type and inclusive date range and returns typed,
whitespace-normalized records without writing to PostgreSQL:

```python
from datetime import date

from app.scraper import scrape_transactions

records = scrape_transactions("il", date(2026, 4, 1), date(2026, 4, 2))
records = scrape_transactions("missed_game", date(2026, 4, 1), date(2026, 4, 2))
```

The recognized source types are `il` for movement to or from the injured/inactive list and
`missed_game` for games missed due to injury. Every record contains the source type, typed date,
Team, Acquired, Relinquished, Notes, and the exact result-page URL from which it was parsed. See
[`legacy/docs/scraper.md`](legacy/docs/scraper.md) for the observed request contract, pagination, parser
structure, access restrictions, and operational safeguards. The full access investigation and
current Prompt 4 status are in
[`legacy/docs/prompt4_access_assessment.md`](legacy/docs/prompt4_access_assessment.md). Do not schedule this
client unless that assessment is resolved with a permitted, reproducible production path.

## Configuration

Copy `.env.example` to `.env` for local development. `DATABASE_URL` uses SQLAlchemy's psycopg 3
dialect. `SCRAPER_USER_AGENT` should identify the deployed scraper and provide appropriate contact
information. Scraper timeout, request interval, retry count, retry backoff, and maximum page count
are also configurable there. The separate `NBA_PDF_*` settings control the official-report client
User-Agent, timeout, request interval, bounded retries, and exponential-backoff base. The checked-in
Compose defaults are local-development credentials only; use secrets in the deployment environment
and never commit them.
