# STAGED CODEX PROMPTS

> **2026-08-11 amendment:** Prompts 1-4 below document the completed legacy/PST baseline. The
> existing Prompt 5 is retired and must not be executed. Official-NBA PDF acquisition, relational
> observations, classification, and episode methodology supersede it; see
> `../../docs/nba_official_methodology.md`. A replacement incremental-update prompt is intentionally
> deferred until the NBA methodology receives a GO decision.

Use these in order. Let Codex finish and test each stage before moving to the next.

## Prompt 1: Repository foundation and characterization tests

Read `MASTER_CODEX_PROMPT.md`, `AGENTS.md`, and everything under `legacy/`.

Do not build the scraper or frontend yet.

Set up the Python project, dependency management, formatting/linting configuration if appropriate, pytest, PostgreSQL configuration, SQLAlchemy, Alembic, and Docker Compose.

Then focus on the legacy processing script. Write characterization/regression tests that capture its current behavior for:
- `extract_injury_info`
- `is_recovery_note`
- `get_nba_season`
- `process_dataset`
- exact duplicate scoring/selection
- time-window deduplication

Because some deduplication logic is nested inside `process_injuries`, you may first test through the public function. Refactor only after tests capture behavior.

Add representative test cases for:
- ACL tear
- MCL/PCL
- Achilles tear
- calf strain
- surgery follow-up
- recovery note
- fracture
- concussion
- illness
- non-injury
- repeated same-body-part injury inside and outside the configured windows

Preserve legacy behavior even where you think a rule is imperfect.

Run the tests and report:
1. files created
2. test results
3. any ambiguities or legacy bugs discovered
4. what you deliberately did not change

## Prompt 2: Database models and historical CSV importer

Implement the PostgreSQL data layer and Alembic migrations described in `MASTER_CODEX_PROMPT.md` and `proposed_schema.sql`.

Create SQLAlchemy models for:
- RawTransaction
- Injury
- UpdateRun

Implement an idempotent historical CSV importer.

CLI example:
`python -m app.jobs.import_csv --source-type il path/to/file.csv`

and:
`python -m app.jobs.import_csv --source-type missed_game path/to/file.csv`

Requirements:
- support columns Date, Team, Acquired, Relinquished, Notes
- validate required columns
- normalize dates safely
- preserve text
- generate deterministic source_row_key values
- repeated import of the same file must insert zero duplicate rows
- output counts for read/inserted/skipped/invalid
- add tests

Do not yet build live scraping.

Run migrations and tests before finishing.

## Prompt 3: Processing service backed by SQL

Port the legacy pipeline into application modules without changing behavior.

The processing layer should read RawTransaction rows and maintain the normalized Injury table.

Requirements:
- preserve source lineage
- preserve IL preference logic
- preserve recovery filtering
- preserve time-window deduplication
- preserve season calculation
- support rebuilding the clean Injury table from raw data
- support targeted reconciliation for a date/player range if practical
- keep the legacy script in `legacy/` for comparison

Add a command:
`python -m app.jobs.rebuild_injuries`

Add a regression test comparing the application implementation against the legacy implementation on the same fixture input.

If the outputs differ, do not mask the difference. Diagnose and document it.

## Prompt 4: ProSportsTransactions scraper

Now implement the live data-acquisition layer.

First inspect the current ProSportsTransactions basketball search/results pages and determine the exact request parameters and pagination behavior for:
1. movement to/from injured/inactive list
2. missed games due to injury

Do not invent selectors or query parameters if you cannot access the site.

Separate:
- HTTP client
- request/query construction
- HTML parser
- pagination
- typed record normalization

Requirements:
- configurable User-Agent
- conservative rate limit
- timeout
- bounded exponential-backoff retry for temporary failures
- no bypassing access controls or anti-bot protections
- preserve source URL
- fail clearly if expected headers/table structure change

Save SMALL sanitized HTML fixtures in `tests/fixtures/` and test parsing offline.

Expose a programmatic function accepting source type + date range and returning normalized raw transaction records.

Do not connect scheduling yet.

## Prompt 5: Incremental update job

Implement:
`python -m app.jobs.update_injuries`

Workflow:
1. create UpdateRun
2. determine safe incremental start date using latest successful run plus a configurable overlap window
3. scrape IL records
4. scrape missed-game records
5. idempotently insert unseen raw transactions
6. update normalized injuries
7. run validation
8. mark UpdateRun successful
9. on exception, mark it failed and return nonzero

Requirements:
- rerunning the same date range must not duplicate data
- failure in one source must not be recorded as overall success
- log useful counts
- allow explicit `--start-date` and `--end-date`
- allow a dry-run if practical
- write tests using mocked HTTP or fixture data

## Prompt 6: FastAPI data API

Build the public read-only API.

Endpoints:
- GET /health
- GET /api/injuries
- GET /api/injuries/export.csv
- GET /api/metadata

`/api/injuries` must support:
- pagination
- player search
- team
- season
- body_part
- injury_type
- start_date
- end_date
- multiple filters together
- deterministic sorting

Return total result count.

`export.csv` must apply the same filters without requiring users to scrape the paginated API.

Add request validation and API tests.

Do not expose raw_transactions.

## Prompt 7: Website

Build the public V1 website against the API.

Required:
- dataset title/description
- last successful update
- total matching records
- filter controls
- player search
- date range
- team
- season
- body part
- injury type
- paginated results
- reset filters
- download current filtered CSV
- mobile-friendly layout
- useful loading/error/empty states

Keep the design simple and data-focused. Avoid dashboards, animations, user accounts, or unnecessary components.

Make URL query parameters reflect filters where practical so views can be shared.

Add basic frontend tests if the chosen stack supports them cleanly.

## Prompt 8: CI, deployment, and operational hardening

Finish production readiness.

Add:
- GitHub Actions test workflow
- production Dockerfiles if needed
- `.env.example`
- deployment instructions
- PostgreSQL backup guidance
- scheduler instructions for daily updates
- structured logging
- scraper/update failure visibility
- health checks
- database indexes review
- security review of public endpoints
- CSV export limits/streaming strategy
- README from fresh clone to working local environment

Do not assume a specific paid host. Document one recommended deployment path and one alternative.

Also add a section documenting:
- source attribution
- scraping assumptions
- rate limiting
- terms/robots review requirement
- public redistribution caveat
- known limitations

Run the full test suite and provide a final project status checklist.

## Prompt 9: Final audit

Perform a critical audit of the repository as if you did not write it.

Check:
- Does a fresh database migrate successfully?
- Can historical CSVs be imported idempotently?
- Can injuries be rebuilt deterministically?
- Does the scraper parser have offline fixtures?
- Does the update job avoid duplicate inserts?
- Are failed runs recorded?
- Do API filters compose correctly?
- Does CSV export exactly match filters?
- Are raw tables private?
- Does the frontend work with realistic record counts?
- Are secrets excluded from git?
- Are operational instructions complete?
- Is every intentional deviation from the legacy pipeline documented?

Fix issues you can prove. Do not make speculative rewrites merely for style.
