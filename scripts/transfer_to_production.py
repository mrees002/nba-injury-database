#!/usr/bin/env python3
"""One-time lean production data transfer utility.

Copy specific tables from the local PostgreSQL database into an
already-bootstrapped production PostgreSQL/Supabase database.

Tables transferred (in order):
    nba_players, nba_teams, nba_schedule_games, public_injury_entries

Tables explicitly NOT transferred:
    update_runs, alembic_version, nba_reports, nba_report_entries,
    nba_injury_conditions, nba_report_candidates, nba_games,
    episode tables, raw_transactions, injuries

Usage:
    python scripts/transfer_to_production.py \\
        --target-database-url postgresql://user:pass@host:5432/db

    # or via environment variable
    TARGET_DATABASE_URL=postgresql://... python scripts/transfer_to_production.py

    # dry-run (report only, no writes)
    python scripts/transfer_to_production.py --target-database-url ... --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import psycopg  # noqa: E402

# Tables to transfer, in FK-safe order (referenced tables first).
TRANSFER_TABLES = [
    "nba_players",
    "nba_teams",
    "nba_schedule_games",
    "public_injury_entries",
]

BATCH_SIZE = 1000

SEASON_RE = re.compile(r"^\d{4}-\d{2}$")


def _validate_season(value: str) -> str:
    """Validate and return a season string like '2018-19'.

    Raises argparse.ArgumentTypeError for malformed values.
    """
    value = value.strip()
    if not SEASON_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"Invalid season format: {value!r}. Expected format: YYYY-YY (e.g. 2018-19)"
        )
    return value


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def normalize_psycopg_url(url: str) -> str:
    """Convert postgresql+psycopg:// to postgresql:// for raw psycopg."""
    return url.replace("postgresql+psycopg://", "postgresql://")


def mask_url(url: str) -> str:
    """Return URL with password redacted for display."""
    try:
        p = urlparse(url)
        if p.password:
            redacted = urlunparse(p._replace(
                netloc=p.netloc.replace(f":{p.password}@", ":***@")
            ))
            return redacted
    except Exception:
        pass
    return url


# ---------------------------------------------------------------------------
# Count helpers
# ---------------------------------------------------------------------------

def _count_rows(conn: psycopg.Connection, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def _count_rows_filtered(
    conn: psycopg.Connection,
    table: str,
    *,
    season: str | None = None,
) -> int:
    with conn.cursor() as cur:
        if season is not None:
            cur.execute(f"SELECT count(*) FROM {table} WHERE season = %s", (season,))
        else:
            cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def _source_counts(
    src: psycopg.Connection,
    *,
    season: str | None = None,
) -> dict[str, int]:
    tables = ["public_injury_entries"] if season else TRANSFER_TABLES
    return {t: _count_rows_filtered(src, t, season=season) for t in tables}


def _dest_counts(
    dst: psycopg.Connection,
    *,
    season: str | None = None,
) -> dict[str, int]:
    tables = ["public_injury_entries"] if season else TRANSFER_TABLES
    return {t: _count_rows_filtered(dst, t, season=season) for t in tables}


# ---------------------------------------------------------------------------
# Core transfer
# ---------------------------------------------------------------------------

def _fetch_columns(cur: psycopg.Cursor, table: str) -> list[str]:
    cur.execute(f"SELECT * FROM {table} LIMIT 0")
    return [desc.name for desc in cur.description]


def _fetch_all(
    cur: psycopg.Cursor,
    table: str,
    *,
    season: str | None = None,
) -> list[tuple]:
    if season is not None:
        cur.execute(f"SELECT * FROM {table} WHERE season = %s", (season,))
    else:
        cur.execute(f"SELECT * FROM {table}")
    return cur.fetchall()


def _build_upsert(table: str, columns: list[str]) -> str:
    """Build an INSERT ... ON CONFLICT DO UPDATE statement."""
    cols = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    update_clauses = [f"{c} = EXCLUDED.{c}" for c in columns if c != "id"]
    update_set = ", ".join(update_clauses)
    return (
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT (id) DO UPDATE SET {update_set}"
    )


def _transfer_table(
    src: psycopg.Connection,
    dst: psycopg.Cursor,
    table: str,
    *,
    dry_run: bool = False,
    season: str | None = None,
) -> dict[str, int]:
    """Transfer one table. Returns dict with source/inserted/updated/dest counts."""
    with src.cursor() as src_cur:
        columns = _fetch_columns(src_cur, table)
        rows = _fetch_all(src_cur, table, season=season)

    source_count = len(rows)
    dest_before = _count_rows(dst.connection, table)

    if dry_run or source_count == 0:
        return {
            "source": source_count,
            "inserted": 0,
            "updated": 0,
            "dest": dest_before,
        }

    sql = _build_upsert(table, columns)
    inserted = 0

    for i in range(0, source_count, BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        # To distinguish inserted vs updated we would need RETURNING.
        # For simplicity, executemany and count total affected rows.
        dst.executemany(sql, batch)
        inserted += len(batch)

    dest_after = _count_rows(dst.connection, table)
    return {
        "source": source_count,
        "inserted": inserted,
        "updated": dest_after - dest_before,
        "dest": dest_after,
    }


def _sync_sequences(
    dst: psycopg.Connection,
    *,
    tables: list[str] | None = None,
) -> None:
    """Reset each table's id sequence to MAX(id) to prevent future collisions."""
    if tables is None:
        tables = TRANSFER_TABLES
    with dst.cursor() as cur:
        for table in tables:
            cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
            max_id = cur.fetchone()[0]
            if max_id <= 0:
                continue

            cur.execute(
                "SELECT pg_get_serial_sequence(%s, 'id')",
                (table,),
            )
            row = cur.fetchone()
            seq_name = row[0] if row else None
            if seq_name:
                cur.execute("SELECT setval(%s, %s)", (seq_name, max_id))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _print_report(
    results: dict[str, dict[str, int]],
    *,
    dry_run: bool = False,
    tables: list[str] | None = None,
) -> None:
    if tables is None:
        tables = TRANSFER_TABLES
    print(f"\n{'Table':<30} {'Source':>8} {'Inserted':>10} {'Updated':>10} {'Dest':>8}")
    print("-" * 70)
    for table in tables:
        r = results[table]
        print(
            f"{table:<30} {r['source']:>8,} {r['inserted']:>10,} "
            f"{r['updated']:>10,} {r['dest']:>8,}"
        )
    print("-" * 70)

    total_source = sum(r["source"] for r in results.values())
    total_dest = sum(r["dest"] for r in results.values())
    print(f"{'TOTAL':<30} {total_source:>8,} {'':>10} {'':>10} {total_dest:>8,}")

    if not dry_run and total_source != total_dest:
        print(f"\n  WARNING: source/dest count mismatch ({total_source} vs {total_dest})")
    elif not dry_run:
        print("\n  Source and destination counts match.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-time lean production data transfer.",
    )
    parser.add_argument(
        "--target-database-url",
        default=os.getenv("TARGET_DATABASE_URL"),
        help="Target PostgreSQL URL (default: $TARGET_DATABASE_URL)",
    )
    parser.add_argument(
        "--source-database-url",
        default=os.getenv("DATABASE_URL"),
        help="Source PostgreSQL URL (default: $DATABASE_URL from config)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report source counts without writing to the target",
    )
    parser.add_argument(
        "--season",
        type=_validate_season,
        default=None,
        help="Transfer only public_injury_entries for this season (e.g. 2018-19). "
             "Skips nba_players, nba_teams, nba_schedule_games.",
    )
    args = parser.parse_args()

    if not args.target_database_url:
        parser.error(
            "Provide --target-database-url or set TARGET_DATABASE_URL."
        )

    # Determine which tables to transfer
    season = args.season
    if season:
        transfer_tables = ["public_injury_entries"]
    else:
        transfer_tables = TRANSFER_TABLES

    # Resolve source URL: CLI > env > config default
    source_url = args.source_database_url
    if not source_url:
        from app.config import get_settings
        source_url = get_settings().database_url

    target_url = args.target_database_url
    src_psy = normalize_psycopg_url(source_url)
    dst_psy = normalize_psycopg_url(target_url)

    print("=" * 64)
    print("LEAN PRODUCTION DATA TRANSFER")
    print("=" * 64)
    print(f"\n  Source:  {mask_url(src_psy)}")
    print(f"  Target:  {mask_url(dst_psy)}")
    print(f"  Tables:  {', '.join(transfer_tables)}")
    if season:
        print(f"  Season:  {season}")
    if args.dry_run:
        print("  Mode:    DRY RUN (no writes)")

    # Connect to source
    print("\nConnecting to source ...")
    src_conn = psycopg.connect(src_psy)
    try:
        src_counts = _source_counts(src_conn, season=season)
        print("  Source connected.")
        for t, c in src_counts.items():
            print(f"    {t}: {c:,} rows")

        if args.dry_run:
            print("\n-- dry-run: skipping target connection and writes --")
            # For dry-run, try to get dest counts if target is reachable
            dest_counts = {}
            try:
                dst_conn = psycopg.connect(dst_psy)
                try:
                    dest_counts = _dest_counts(dst_conn, season=season)
                finally:
                    dst_conn.close()
            except Exception:
                pass

            results = {}
            for t in transfer_tables:
                dest_count = dest_counts.get(t, 0)
                results[t] = {
                    "source": src_counts.get(t, 0),
                    "inserted": 0,
                    "updated": 0,
                    "dest": dest_count,
                }
            _print_report(results, dry_run=True, tables=transfer_tables)
            return 0

        # Connect to target
        print("\nConnecting to target ...")
        dst_conn = psycopg.connect(dst_psy)
        try:
            print("  Target connected.")

            # Verify target tables exist
            with dst_conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
                existing = {row[0] for row in cur.fetchall()}
            missing = set(transfer_tables) - existing
            if missing:
                print(f"\n  ERROR: target missing tables: {sorted(missing)}")
                print("  Run bootstrap_lean_production.py first.")
                return 1

            # Transfer each table
            results = {}
            for table in transfer_tables:
                print(f"\n  Transferring {table} ...")
                r = _transfer_table(
                    src_conn, dst_conn.cursor(), table, season=season,
                )
                results[table] = r
                print(
                    f"    source={r['source']:,}  inserted={r['inserted']:,}  "
                    f"updated={r['updated']:,}  dest={r['dest']:,}"
                )

            # Sync sequences for transferred tables
            print("\n  Synchronizing sequences ...")
            _sync_sequences(dst_conn, tables=transfer_tables)

            dst_conn.commit()
            print("  Committed.")
        finally:
            dst_conn.close()

        _print_report(results, tables=transfer_tables)

        src_total = sum(src_counts.values())
        dst_total = sum(r["dest"] for r in results.values())
        return 0 if src_total == dst_total else 1
    finally:
        src_conn.close()


if __name__ == "__main__":
    sys.exit(main())
