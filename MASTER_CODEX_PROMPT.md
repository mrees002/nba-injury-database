# MASTER CODEX PROMPT

You are working in a new repository for a public NBA injury dataset application.

## Objective

Build a production-quality system that:

1. Collects NBA injury transaction records from ProSportsTransactions.com.
2. Preserves the raw scraped records in PostgreSQL.
3. Reuses and carefully ports the existing injury classification and deduplication logic in `legacy/process_injuries_pipeline.py`.
4. Produces a normalized, duplicate-free `injuries` dataset.
5. Exposes the dataset through a web application where users can search, filter, and download CSV data.
6. Supports automated incremental updates without re-scraping the full history every run.
7. Includes tests, logging, validation, and documentation.

## Important constraints

- Treat `legacy/process_injuries_pipeline.py` as the behavioral reference for classification, season assignment, and deduplication.
- Do not silently change classification or deduplication behavior.
- If you believe a rule should change, preserve the current behavior first, add a test demonstrating it, and document the proposed change separately.
- Raw scraped data must be immutable after ingestion except for explicit migrations/fixes.
- Avoid storing CSV files as the application's source of truth. PostgreSQL is the source of truth.
- Scraping must be polite and conservative. Respect robots.txt, site terms, server load, and retry/backoff limits.
- Do not bypass CAPTCHAs, authentication, access controls, or anti-bot restrictions.
- Store the source URL and scrape timestamp for every raw record.
- Design ingestion to be idempotent.
- Never delete historical records merely because a later scrape does not return them.
- The application must be able to export filtered results as CSV.

## Preferred stack

Backend:
- Python 3.12+
- FastAPI
- SQLAlchemy 2.x
- Alembic
- PostgreSQL
- Pydantic
- httpx
- BeautifulSoup4
- pytest

Frontend:
- Prefer a simple maintainable implementation.
- Next.js + TypeScript is acceptable.
- If a server-rendered FastAPI/Jinja implementation materially reduces complexity for V1, choose that and document the tradeoff.

Infrastructure:
- Docker Compose for local development
- `.env.example`
- GitHub Actions for tests
- A scheduled update entry point suitable for GitHub Actions, Render Cron, Railway Cron, or another scheduler

## Required data model

At minimum, create:

### raw_transactions
- id
- source_type (`il` or `missed_game`)
- transaction_date
- team
- acquired
- relinquished
- notes
- source_url
- source_row_key or source_hash
- scraped_at
- created_at

Enforce an idempotency strategy so the same source record cannot be inserted repeatedly.

### injuries
- id
- date
- season
- player_name
- team
- body_part
- injury_type
- notes
- preferred_source
- source_raw_transaction_id
- created_at
- updated_at

Also create a scrape/update run table recording:
- start/end time
- requested date range
- rows fetched
- rows inserted
- rows processed
- status
- error details

## Existing behavior to preserve

The legacy script:
- reads two source types: IL transactions and missed games due to injury
- processes `Relinquished` rows as injury occurrences
- ignores return/acquired-only rows
- classifies body part and injury type from notes
- prefers IL records when exact duplicates occur
- filters recovery notes
- performs time-window deduplication
- assigns NBA seasons based on the transaction date

Read the implementation directly. Build regression tests before refactoring it.

## Architecture

Use modules similar to:

app/
  api/
  db/
  models/
  schemas/
  scraper/
  processing/
  services/
  jobs/

tests/
  fixtures/
  scraper/
  processing/
  api/

legacy/

The exact structure can differ if there is a strong reason.

## Required API behavior

Implement endpoints for:
- health check
- list injuries with pagination
- filter by player
- filter by team
- filter by season
- filter by body part
- filter by injury type
- filter by date range
- combine filters
- download filtered CSV
- metadata endpoint returning available seasons/teams/body parts/injury types and last successful update time

Do not expose raw transaction tables publicly in V1.

## Required user interface

Create a clean desktop/mobile page with:
- title and short dataset description
- last updated timestamp
- total result count
- filters
- searchable/paginated results table
- clear filters button
- download CSV button

The initial site does not need authentication or user accounts.

## Scraping workflow

Build the scraper separately from the processing layer.

The scraper should:
1. Accept source type and date range.
2. Construct the appropriate request(s).
3. Parse paginated result tables.
4. Normalize whitespace only. Preserve original text.
5. Return typed records.
6. Retry temporary network errors with bounded exponential backoff.
7. Rate limit requests.
8. Stop and report clearly if the site's page structure has changed.
9. Include HTML fixture tests so parser behavior can be tested without live requests.

Before hardcoding selectors, inspect the actual source pages. If network access is unavailable in the current Codex environment, create the scraper interface and fixture-driven parser, document what remains to verify, and do not invent selectors.

## Incremental update workflow

Create a command such as:

python -m app.jobs.update_injuries

It should:
1. Read the latest successful scrape state.
2. Use a small configurable overlap window so late corrections can be captured.
3. Scrape both source types.
4. Insert only unseen raw rows.
5. Run processing for affected data.
6. Reconcile deduplication where newly inserted rows may affect existing injury events.
7. Run validation checks.
8. Record update-run metrics.
9. Exit nonzero on failure.

The overlap must be safe because ingestion is idempotent.

## Migration of historical data

Do not assume the old CSV files listed in `legacy/UPLOADED_FILES_REFERENCE.txt` are present.

Create import commands/interfaces so that, when historical CSVs are supplied, they can be loaded into `raw_transactions`.

The importer must accept the legacy five-column format:
- Date
- Team
- Acquired
- Relinquished
- Notes

It should identify whether the file is IL or missed-games data through an explicit CLI argument rather than guessing whenever possible.

## Testing requirements

Before replacing legacy processing behavior:
- add unit tests for injury extraction
- add season assignment tests
- add exact-dedup scoring tests
- add time-window dedup tests
- add regression fixtures for difficult cases such as ACL tear -> surgery, Achilles tear -> follow-up, repeated soreness/sprains, recovery notes, illness, and non-injury rows

Also test:
- parser against saved HTML fixtures
- idempotent raw ingestion
- combined API filters
- CSV export
- failed scrape run recording
- successful incremental rerun inserting zero duplicates

## Definition of done

Do not call the project complete until:
- local setup works from README
- database migrations work from an empty DB
- tests pass
- scraper parser has fixture coverage
- historical CSV importer works
- update job is idempotent
- API filters work
- CSV download works
- frontend works
- Docker Compose works
- `.env.example` exists
- CI test workflow exists
- deployment instructions exist
- limitations and scraping/legal assumptions are documented

## Execution behavior

Work incrementally.
Run tests after each meaningful implementation stage.
Do not rewrite the entire legacy pipeline before capturing its behavior in tests.
Commit-sized changes are preferred.
When blocked by missing external access or missing historical CSV files, complete everything that can be completed, leave explicit TODOs, and state precisely what input is required.
