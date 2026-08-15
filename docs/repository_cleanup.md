# Repository cleanup

Cleanup date: 2026-08-12

## Scope and active direction

This cleanup reorganized documentation and removed ignored generated artifacts. It did not
redesign packages, change processing rules, rewrite migrations, delete uncommitted work, or mutate
PostgreSQL.

The active production direction is:

`NBA PDFs -> discovery/download -> raw parsing -> PostgreSQL -> derived classification -> InjuryEpisode methodology -> API -> website -> scheduled incremental updates`

Raw NBA archive ingestion and validation must be completed before further classification,
episode, deduplication, or PST benchmark tuning. PST remains a benchmark, not a future production
source.

## Kept active

- Root operating files: `AGENTS.md`, `README.md`, `.env.example`, `.gitignore`, `pyproject.toml`,
  `compose.yaml`, and `alembic.ini`.
- All Alembic infrastructure and migrations, including the pending additive migration
  `0004_candidate_report_lineage`.
- Shared database/session/configuration and all SQLAlchemy models.
- `app/nba/`, official-NBA jobs, fixtures, tests, and `docs/nba_official_methodology.md`.
- The existing uncommitted NBA implementation in full.

## Kept as legacy or benchmark

- `legacy/process_injuries_pipeline.py`, `WEB_SCRAPER_MIGRATION_GUIDE.md`, and
  `UPLOADED_FILES_REFERENCE.txt`.
- The historical CSV importer, SQL-backed PST processing, legacy models/tables, and related jobs.
- `app/scraper/`, retained at its stable import path because tests and diagnostics depend on it.
- PST importer/processing/scraper fixtures and tests.
- `app/jobs/benchmark_nba_vs_pst.py`, which intentionally compares the two methodologies.
- All PST regression, access, and scraper documentation now grouped under `legacy/docs/`.

## Files moved

| Previous path | New path | Reason |
| --- | --- | --- |
| `CODEX_PROMPTS.md` | `legacy/specifications/CODEX_PROMPTS.md` | Retired staged prompts |
| `MASTER_CODEX_PROMPT.md` | `legacy/specifications/MASTER_CODEX_PROMPT.md` | Superseded PST-oriented product specification |
| `README_START_HERE.md` | `legacy/specifications/README_START_HERE.md` | Historical starter-kit workflow |
| `proposed_schema.sql` | `legacy/specifications/proposed_schema.sql` | Historical PST schema proposal; migrations are authoritative |
| `docs/legacy_regression.md` | `legacy/docs/legacy_regression.md` | PST benchmark documentation |
| `docs/scraper.md` | `legacy/docs/scraper.md` | Blocked PST scraper contract |
| `docs/prompt4_access_assessment.md` | `legacy/docs/prompt4_access_assessment.md` | PST access investigation |
| `scripts/diagnose_pst_access.py` | `legacy/scripts/diagnose_pst_access.py` | Manual PST-only diagnostic |

Incoming documentation references were updated. `legacy/README.md` now maps benchmark code that
must remain at stable application import paths, and `legacy/specifications/README.md` warns that
the archived prompts are not current instructions.

## Files deleted

No source, test, fixture, migration, documentation content, database row, or Docker volume was
deleted. Only ignored generated artifacts were removed:

- two `.DS_Store` files;
- `.pytest_cache/` and `.ruff_cache/`;
- `nba_injury_database.egg-info/`;
- repository-local `__pycache__/` directories and compiled bytecode.

These are reproducible outputs, are already covered by `.gitignore`, and contain no project state.

## Intentionally retained in place

- `app/scraper/`, `app/processing/`, `app/services/csv_import.py`, legacy jobs, and their tests look
  non-production but remain importable for PST reproduction and the NBA-vs-PST benchmark.
- The two old source-workflow reference files under `legacy/` remain because they document the
  historical inputs and decisions.
- All uncommitted work remains in the working tree. No reset, checkout, staging, or commit was
  performed.
- The local `.venv` remains because it is the working development environment and is ignored.

## Verification

- Full test suite: **131 passed**.
- Ruff lint: **passed**.
- Ruff formatting: initial check identified two files from the interrupted feature work; both were
  formatted and the final check passed.
- Docker Compose: PostgreSQL 16 service **healthy**; no volume operation was run.
- Alembic check: correctly reports **target database is not up to date**. PostgreSQL remains at
  `0003_report_previous_status`; preserved migration `0004_candidate_report_lineage` is pending and
  was deliberately not applied because cleanup did not authorize database changes.

## Database integrity

The retained PST benchmark is unchanged:

- `raw_transactions`: **44,126**
- `injuries`: **12,877**

Stable NBA state after stopping acquisition:

- report candidates: **12,114**
- reports: **8,326**
- parsed reports: **8,326**, all parser version `nba-pdf-v4`
- report entries: **735,500**
- injury conditions: **657,836**
- injury episodes: **0**
- stored PDF bytes: **393,985,574**
- candidate states: 8,141 parsed, 3,786 discovered, 185 parse-failed labels, 2 HTTP-failed

No cleanup operation issued SQL writes. During verification, the first interrupt was found to have
stopped a wrapper while an inherited acquisition child continued; the child had advanced the
checkpoint beyond the last pre-cleanup observation before it exited. No backfill process remains,
and report/entry/discovered counts were identical in two checks five seconds apart.

## Readiness

The repository is ready for a separate handoff task. That handoff should call out the pending
`0004` migration and the stopped, incomplete raw archive backfill. This cleanup intentionally did
not create `HANDOFF.md` or `handoff_state.json`.
