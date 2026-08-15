from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date

from sqlalchemy import text

from app.db.session import build_engine, build_session_factory
from app.nba.reparse import reparse_reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Reparse saved NBA PDFs with the current parser")
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2018, 12, 20))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 4, 12))
    args = parser.parse_args()
    if args.end_date < args.start_date:
        parser.error("--end-date must not be before --start-date")
    engine = build_engine()
    session_factory = build_session_factory(engine)
    lock_connection = engine.connect()
    lock_key = 2_026_081_201
    lock_acquired = False
    try:
        if engine.dialect.name == "postgresql":
            lock_acquired = bool(
                lock_connection.scalar(
                    text("select pg_try_advisory_lock(:lock_key)"), {"lock_key": lock_key}
                )
            )
            if not lock_acquired:
                raise RuntimeError(
                    "Another NBA report backfill or reparse process is already running"
                )
        with session_factory() as session:
            result = reparse_reports(session, args.start_date, args.end_date)
    finally:
        if lock_acquired:
            lock_connection.execute(
                text("select pg_advisory_unlock(:lock_key)"), {"lock_key": lock_key}
            )
        lock_connection.close()
    print(json.dumps(asdict(result), sort_keys=True))


if __name__ == "__main__":
    main()
