# NBA Injury Database

This repository is being built in staged, testable increments. Prompt 1 establishes the
Python/PostgreSQL foundation and locks down the behavior of the legacy processing pipeline.
The legacy code remains the behavioral reference; application models, data import, scraping,
API, and UI are intentionally deferred to later prompts.

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

Alembic is configured in this stage, but the first schema migration is intentionally part of
Prompt 2 alongside the SQLAlchemy models.

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

## Configuration

Copy `.env.example` to `.env` for local development. `DATABASE_URL` uses SQLAlchemy's psycopg 3
dialect. The checked-in Compose defaults are local-development credentials only; use secrets in
the deployment environment and never commit them.

