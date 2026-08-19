#!/usr/bin/env python3
"""Bootstrap a lean Supabase production database.

Applies ``scripts/lean_production_bootstrap.sql`` to the target PostgreSQL
database, creating ONLY the tables required by the public API and the
incremental update job.

Tables created:
    nba_players, nba_teams, nba_schedule_games, update_runs,
    public_injury_entries, alembic_version

No archive, discovery, parsing, or episode tables are created.

Usage:
    # Via environment variable
    DATABASE_URL=postgresql://... python scripts/bootstrap_lean_production.py

    # Via CLI argument  (takes precedence)
    python scripts/bootstrap_lean_production.py --database-url postgresql://...

    # Validate only (skip actual bootstrap)
    python scripts/bootstrap_lean_production.py --validate-only
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SQL_BOOTSTRAP = PROJECT_ROOT / "scripts" / "lean_production_bootstrap.sql"

EXPECTED_TABLES = frozenset(
    [
        "nba_players",
        "nba_teams",
        "nba_schedule_games",
        "update_runs",
        "public_injury_entries",
        "alembic_version",
    ]
)


def _load_sql() -> str:
    return SQL_BOOTSTRAP.read_text()


def _connect(database_url: str):
    """Return a psycopg connection, preferring psycopg3 (v3) then v2."""
    normalized = database_url.replace("postgresql+psycopg://", "postgresql://")
    try:
        import psycopg  # psycopg 3.x

        return psycopg.connect(normalized)
    except ImportError:
        import psycopg2  # type: ignore[no-redef]

        return psycopg2.connect(normalized)


def _table_names(cursor) -> frozenset[str]:
    """Return the set of user-table names in the current database."""
    cursor.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public'"
    )
    return frozenset(row[0] for row in cursor.fetchall())


def _alembic_head(cursor) -> str | None:
    cursor.execute("SELECT version_num FROM alembic_version LIMIT 1")
    row = cursor.fetchone()
    return row[0] if row else None


def bootstrap(database_url: str, *, validate_only: bool = False) -> None:
    sql = _load_sql()

    print(f"Target: {database_url}")

    with _connect(database_url) as conn:
        conn.autocommit = False
        cur = conn.cursor()

        if validate_only:
            print("\n-- validate-only: checking current schema --")
            tables = _table_names(cur)
            missing = EXPECTED_TABLES - tables
            extra = tables - EXPECTED_TABLES
            head = _alembic_head(cur)

            print(f"  alembic_version head: {head}")
            print(f"  tables present:       {sorted(tables)}")
            if missing:
                print(f"  MISSING tables:       {sorted(missing)}")
            if extra:
                print(f"  EXTRA tables:         {sorted(extra)}")
            ok = not missing and not extra and head == "0007_public_injury_entries"
            print(f"  valid: {ok}")
            return

        print("\nApplying lean-production bootstrap SQL ...")
        cur.execute(sql)
        conn.commit()
        print("Done.")

        # Verify
        tables = _table_names(cur)
        head = _alembic_head(cur)
        print(f"\n  alembic_version head: {head}")
        print(f"  tables created:       {sorted(tables & EXPECTED_TABLES)}")

        unexpected = tables - EXPECTED_TABLES
        if unexpected:
            print(f"  WARNING – unexpected tables: {sorted(unexpected)}")

        missing = EXPECTED_TABLES - tables
        if missing:
            print(f"  ERROR – missing tables: {sorted(missing)}")
            sys.exit(1)

        print("\nLean production database ready.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap a lean Supabase production database."
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="PostgreSQL connection URL (default: $DATABASE_URL)",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Check current schema without applying changes",
    )
    args = parser.parse_args()

    if not args.database_url:
        parser.error(
            "Provide --database-url or set the DATABASE_URL environment variable."
        )

    bootstrap(args.database_url, validate_only=args.validate_only)


if __name__ == "__main__":
    main()
