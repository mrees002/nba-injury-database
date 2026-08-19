#!/usr/bin/env python3
"""Lean-production validation workflow.

Creates a temporary PostgreSQL database (``nba_injuries_lean``) using the
standalone SQL bootstrap (``scripts/lean_production_bootstrap.sql``), copies
production data from the archive database, then validates that the API can
serve all public endpoints against that database.

The archive database is never modified.

Tables created in the lean database:
    nba_players, nba_teams, nba_schedule_games, update_runs,
    public_injury_entries, alembic_version

Usage:
    python scripts/validate_lean_production.py          # full workflow
    python scripts/validate_lean_production.py --stats  # skip API test, just report
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import psycopg  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ARCHIVE_URL = os.getenv(
    "ARCHIVE_DATABASE_URL",
    "postgresql://nba:nba@localhost:5432/nba_injuries",
)
LEAN_DB_NAME = "nba_injuries_lean"
# psycopg uses plain postgresql:// URLs
LEAN_PSYCOPG_URL = f"postgresql://nba:nba@localhost:5432/{LEAN_DB_NAME}"
# SQLAlchemy/Alembic uses postgresql+psycopg:// URLs
LEAN_URL = f"postgresql+psycopg://nba:nba@localhost:5432/{LEAN_DB_NAME}"

# Tables to copy from archive (production tables only)
# Order respects foreign-key dependencies: referenced tables first.
PRODUCTION_TABLES = [
    "nba_players",
    "nba_teams",
    "nba_schedule_games",
    "update_runs",
    "public_injury_entries",
]

SQL_BOOTSTRAP = PROJECT_ROOT / "scripts" / "lean_production_bootstrap.sql"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _server_url(url: str) -> str:
    """Return connection URL pointing at the default 'postgres' database."""
    p = urlparse(url)
    return urlunparse(p._replace(path="/postgres"))


def _drop_lean(cursor: psycopg.Cursor) -> None:
    """Terminate existing connections and drop the lean database."""
    cursor.execute(
        "SELECT pg_terminate_backend(pid) "
        "FROM pg_stat_activity "
        "WHERE datname = %s",
        (LEAN_DB_NAME,),
    )
    cursor.execute(f"DROP DATABASE IF EXISTS {LEAN_DB_NAME}")
    print(f"  Dropped database '{LEAN_DB_NAME}' (if it existed)")


def _create_lean(cursor: psycopg.Cursor) -> None:
    cursor.execute(f"CREATE DATABASE {LEAN_DB_NAME}")
    print(f"  Created database '{LEAN_DB_NAME}'")


def _apply_lean_bootstrap() -> None:
    """Execute the lean-production SQL bootstrap against the lean database."""
    sql = SQL_BOOTSTRAP.read_text()
    with psycopg.connect(LEAN_PSYCOPG_URL) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
    print("  Applied lean-production SQL bootstrap")


def _copy_table(cursor: psycopg.Cursor, table: str) -> int:
    """Copy all rows from a table in the archive DB to the lean DB.

    Reads all rows from archive via a separate connection, then inserts
    into lean using executemany with parameterized queries.
    Returns row count.
    """
    with psycopg.connect(ARCHIVE_URL) as archive_conn:
        archive_cur = archive_conn.cursor()
        archive_cur.execute(f"SELECT * FROM {table}")
        col_names = [desc.name for desc in archive_cur.description]
        rows = archive_cur.fetchall()

    total = len(rows)
    if total == 0:
        print(f"    {table}: 0 rows (empty)")
        return 0

    columns = ", ".join(col_names)
    placeholders = ", ".join(["%s"] * len(col_names))
    insert_sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"

    # executemany in batches for performance
    batch_size = 1000
    for i in range(0, total, batch_size):
        batch = rows[i : i + batch_size]
        cursor.executemany(insert_sql, batch)

    print(f"    {table}: {total:,} rows")
    return total


def _copy_all_production_data() -> dict[str, int]:
    """Copy all production tables from archive to lean. Returns row counts."""
    counts = {}
    with psycopg.connect(LEAN_PSYCOPG_URL) as lean_conn:
        lean_cur = lean_conn.cursor()
        for table in PRODUCTION_TABLES:
            counts[table] = _copy_table(lean_cur, table)
        lean_conn.commit()
    return counts


def _report_size(cursor: psycopg.Cursor) -> str:
    cursor.execute(
        "SELECT pg_size_pretty(pg_database_size(%s))",
        (LEAN_DB_NAME,),
    )
    return cursor.fetchone()[0]


def _report_row_counts(cursor: psycopg.Cursor) -> dict[str, int]:
    counts = {}
    for table in PRODUCTION_TABLES:
        try:
            cursor.execute(f"SELECT count(*) FROM {table}")
            counts[table] = cursor.fetchone()[0]
        except Exception:
            counts[table] = -1
    return counts


def _report_alembic_head(cursor: psycopg.Cursor) -> str | None:
    try:
        cursor.execute("SELECT version_num FROM alembic_version LIMIT 1")
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# API validation
# ---------------------------------------------------------------------------

def _validate_api() -> list[str]:
    """Test API endpoints against the lean DB. Returns list of error strings."""
    from fastapi.testclient import TestClient

    from app.api import app, get_session
    from app.db.session import build_engine, build_session_factory

    errors = []
    lean_engine = build_engine(LEAN_URL)
    lean_factory = build_session_factory(lean_engine)

    def _override():
        with lean_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            tests = [
                ("GET /", "/"),
                ("GET /injuries", "/injuries"),
                ("GET /injuries.csv", "/injuries.csv"),
                ("GET /players", "/players"),
                ("GET /teams", "/teams"),
                ("GET /api/players/1", "/api/players/1"),
            ]
            for label, path in tests:
                try:
                    resp = client.get(path)
                    if resp.status_code in (200, 404):
                        print(f"    {label} -> {resp.status_code} OK")
                    else:
                        errors.append(f"{label} -> {resp.status_code}")
                        print(f"    {label} -> {resp.status_code} FAIL")
                except Exception as exc:
                    errors.append(f"{label} -> EXCEPTION: {exc}")
                    print(f"    {label} -> EXCEPTION: {exc}")
    finally:
        app.dependency_overrides.clear()
        lean_engine.dispose()

    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Lean-production validation")
    parser.add_argument("--stats", action="store_true", help="Only report stats, skip API")
    args = parser.parse_args()

    print("=" * 64)
    print("LEAN-PRODUCTION VALIDATION WORKFLOW")
    print("=" * 64)

    # Step 1: Create lean database
    print("\n[1/5] Creating lean database...")
    with psycopg.connect(_server_url(LEAN_PSYCOPG_URL)) as server_conn:
        server_conn.autocommit = True
        cur = server_conn.cursor()
        _drop_lean(cur)
        _create_lean(cur)

    # Step 2: Apply lean-production SQL bootstrap
    print("\n[2/5] Applying lean-production SQL bootstrap...")
    _apply_lean_bootstrap()

    # Step 3: Copy production data
    print("\n[3/5] Copying production tables from archive...")
    _copy_all_production_data()

    # Step 4: Report database stats
    print("\n[4/5] Lean database statistics:")
    with psycopg.connect(LEAN_PSYCOPG_URL) as lean_conn:
        cur = lean_conn.cursor()
        size = _report_size(cur)
        row_counts = _report_row_counts(cur)
        alembic_head = _report_alembic_head(cur)

    print(f"\n  Database size:      {size}")
    print(f"  Alembic version:    {alembic_head}")
    print("\n  Production tables:")
    for table, count in row_counts.items():
        print(f"    {table:30s} {count:>8,} rows")

    if args.stats:
        print("\n  (--stats: skipping API validation)")
        return 0

    # Step 5: Validate API endpoints
    print("\n[5/5] Validating API endpoints against lean database...")
    api_errors = _validate_api()

    # Step 6: Run pytest API tests against lean database
    print("\n[6/5] Running pytest test suite against lean database...")
    env = os.environ.copy()
    env["DATABASE_URL"] = LEAN_URL
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_api.py", "-v", "--tb=short", "-q"],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    # Show last portion of output (most relevant)
    stdout = result.stdout
    if len(stdout) > 3000:
        stdout = "...(truncated)...\n" + stdout[-3000:]
    print(stdout)
    if result.returncode != 0 and result.stderr:
        stderr = result.stderr
        if len(stderr) > 1500:
            stderr = stderr[-1500:]
        print("STDERR:", stderr)

    # Summary
    print("\n" + "=" * 64)
    print("SUMMARY")
    print("=" * 64)
    print(f"  Lean DB size:      {size}")
    print(f"  Alembic version:   {alembic_head}")
    print(f"  Tables created:    {len(PRODUCTION_TABLES)}")

    if api_errors:
        print(f"\n  API ERRORS ({len(api_errors)}):")
        for e in api_errors:
            print(f"    - {e}")
    else:
        print("\n  API validation:    ALL ENDPOINTS PASS")

    if result.returncode == 0:
        print("  Pytest API:        ALL TESTS PASS")
    else:
        print(f"  Pytest API:        FAILED (exit code {result.returncode})")

    print("\n  Archive database:  UNCHANGED")
    print("=" * 64)

    return 1 if api_errors or result.returncode != 0 else 0


if __name__ == "__main__":
    sys.exit(main())
