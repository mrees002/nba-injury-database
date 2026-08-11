# NBA Injury Database

This repository is being built in staged, testable increments. The Python/PostgreSQL foundation,
legacy processing characterization tests, SQLAlchemy data model, initial migration, and historical
CSV importer are implemented. The legacy code remains the behavioral reference; processing from
PostgreSQL, live scraping, API, and UI are intentionally deferred to later prompts.

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

This creates the `raw_transactions`, `injuries`, and `update_runs` tables and their indexes and
constraints.

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
[`docs/legacy_regression.md`](docs/legacy_regression.md).

## Configuration

Copy `.env.example` to `.env` for local development. `DATABASE_URL` uses SQLAlchemy's psycopg 3
dialect. The checked-in Compose defaults are local-development credentials only; use secrets in
the deployment environment and never commit them.
