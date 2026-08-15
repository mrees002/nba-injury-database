# Legacy and benchmark material

This directory is retained for historical reproducibility and methodology comparison. It is not
the active production ingestion path.

## PST benchmark implementation

- `process_injuries_pipeline.py` is the original behavioral reference.
- `app/processing/`, `app/services/csv_import.py`, `app/jobs/import_csv.py`, and
  `app/jobs/rebuild_injuries.py` remain at stable import paths because the regression suite and
  historical rebuild depend on them.
- `app/scraper/` is the blocked, offline-tested PST client. It is retained for audit and comparison,
  not scheduling or production ingestion.
- `tests/processing/`, `tests/importing/`, `tests/scraper/`, and `tests/fixtures/scraper/` preserve
  reproducibility.
- `app/jobs/benchmark_nba_vs_pst.py` intentionally bridges the active NBA-derived episode layer and
  the retained PST benchmark.

## Directory contents

- `docs/` contains the legacy full-data regression, PST scraper contract, and access assessment.
- `scripts/` contains manual PST diagnostics that must not bypass access controls.
- `specifications/` contains retired staged Codex prompts and the original PST-oriented schema
  proposal. They are historical context, not current instructions.
- `WEB_SCRAPER_MIGRATION_GUIDE.md` and `UPLOADED_FILES_REFERENCE.txt` describe the pre-database
  workflow and source-file inventory.

The current architecture and operating rules are in `../README.md`, `../AGENTS.md`, and
`../docs/nba_official_methodology.md`. Raw NBA ingestion should be completed and validated before
any further derived classification or episode tuning.
