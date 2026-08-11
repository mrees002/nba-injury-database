# AGENTS.md

## Project mission
Maintain a reproducible, transparent NBA injury dataset pipeline and public download site.

## Non-negotiable rules
1. PostgreSQL is the source of truth. Do not make appended CSV files the primary datastore.
2. `legacy/process_injuries_pipeline.py` is the behavioral baseline until regression tests prove equivalence.
3. Never silently modify deduplication or injury classification rules.
4. Every scraper ingestion path must be idempotent.
5. Preserve raw source text and source URLs.
6. Do not bypass anti-bot protections, authentication, CAPTCHAs, or access controls.
7. Keep network-dependent scraper code separable from HTML parsing so tests can run offline.
8. Add or update tests for every processing-rule change.
9. Prefer clear, ordinary code over unnecessary abstraction.
10. Never commit secrets.

## Commands Codex should keep working
- `pytest`
- database migration command documented in README
- historical import command
- incremental update command
- local app startup command
- Docker Compose startup

## Processing changes
If changing classification or deduplication:
- first write a failing or characterization test
- explain the behavior being changed
- preserve raw source lineage
- document any intentional dataset-count difference

## Scraper changes
- use bounded retries
- use rate limiting
- identify the scraper with a configurable User-Agent
- fail loudly when expected table structure changes
- use saved HTML fixtures in tests

## Repository hygiene
- keep generated data, secrets, local DB files, downloaded HTML, and large exports out of git unless they are intentional small test fixtures
- update `.env.example` and README whenever configuration changes
