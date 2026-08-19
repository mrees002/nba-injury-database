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


def _source_counts(src: psycopg.Connection) -> dict[str, int]:
    return {t: _count_rows(src, t) for t in TRANSFER_TABLES}


def _dest_counts(dst: psycopg.Connection) -> dict[str, int]:
    return {t: _count_rows(dst, t) for t in TRANSFER_TABLES}


# ---------------------------------------------------------------------------
# Core transfer
# ---------------------------------------------------------------------------

def _fetch_columns(cur: psycopg.Cursor, table: str) -> list[str]:
    cur.execute(f"SELECT * FROM {table} LIMIT 0")
    return [desc.name for desc in cur.description]


def _fetch_all(cur: psycopg.Cursor, table: str) -> list[tuple]:
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
) -> dict[str, int]:
    """Transfer one table. Returns dict with source/inserted/updated/dest counts."""
    with src.cursor() as src_cur:
        columns = _fetch_columns(src_cur, table)
        rows = _fetch_all(src_cur, table)

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


def _sync_sequences(dst: psycopg.Connection) -> None:
    """Reset each table's id sequence to MAX(id) to prevent future collisions."""
    with dst.cursor() as cur:
        for table in TRANSFER_TABLES:
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

def _print_report(results: dict[str, dict[str, int]], *, dry_run: bool = False) -> None:
    print(f"\n{'Table':<30} {'Source':>8} {'Inserted':>10} {'Updated':>10} {'Dest':>8}")
    print("-" * 70)
    for table in TRANSFER_TABLES:
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
    args = parser.parse_args()

    if not args.target_database_url:
        parser.error(
            "Provide --target-database-url or set TARGET_DATABASE_URL."
        )

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
    print(f"  Tables:  {', '.join(TRANSFER_TABLES)}")
    if args.dry_run:
        print("  Mode:    DRY RUN (no writes)")

    # Connect to source
    print("\nConnecting to source ...")
    src_conn = psycopg.connect(src_psy)
    try:
        src_counts = _source_counts(src_conn)
        print("  Source connected.")
        for t, c in src_counts.items():
            print(f"    {t}: {c:,} rows")

        if args.dry_run:
            print("\n-- dry-run: skipping target connection and writes --")
            _print_report(
                {t: {"source": src_counts[t], "inserted": 0, "updated": 0, "dest": 0}
                 for t in TRANSFER_TABLES},
                dry_run=True,
            )
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
            missing = set(TRANSFER_TABLES) - existing
            if missing:
                print(f"\n  ERROR: target missing tables: {sorted(missing)}")
                print("  Run bootstrap_lean_production.py first.")
                return 1

            # Transfer each table
            results = {}
            for table in TRANSFER_TABLES:
                print(f"\n  Transferring {table} ...")
                r = _transfer_table(src_conn, dst_conn.cursor(), table)
                results[table] = r
                print(
                    f"    source={r['source']:,}  inserted={r['inserted']:,}  "
                    f"updated={r['updated']:,}  dest={r['dest']:,}"
                )

            # Sync sequences
            print("\n  Synchronizing sequences ...")
            _sync_sequences(dst_conn)

            dst_conn.commit()
            print("  Committed.")
        finally:
            dst_conn.close()

        _print_report(results)

        src_total = sum(src_counts.values())
        dst_total = sum(r["dest"] for r in results.values())
        return 0 if src_total == dst_total else 1
    finally:
        src_conn.close()


if __name__ == "__main__":
    sys.exit(main())
