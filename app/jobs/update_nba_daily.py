from __future__ import annotations

import sys
from datetime import date, datetime, timezone

from app.db.session import build_engine, build_session_factory
from app.jobs.backfill_nba_reports import main as backfill_main
from app.models.update_run import UpdateRun


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def run_daily_update(
    start_date: date,
    end_date: date,
    *,
    registered_only: bool = False,
) -> None:
    """Run the daily NBA injury update with UpdateRun lifecycle tracking.

    Creates an UpdateRun record, invokes the backfill, and marks the run
    completed or failed.  This is the programmatic entry point used by
    ``run_daily_pipeline`` so that UpdateRun bookkeeping is not duplicated.
    """
    engine = build_engine()
    session_factory = build_session_factory(engine)

    with session_factory() as session:
        run = UpdateRun(
            requested_start_date=start_date,
            requested_end_date=end_date,
            status="started",
        )
        session.add(run)
        session.commit()

        original_argv = sys.argv[:]
        try:
            backfill_argv = [
                "backfill_nba_reports",
                "--start-date", start_date.isoformat(),
                "--end-date", end_date.isoformat(),
            ]
            if registered_only:
                backfill_argv.append("--registered-only")
            else:
                backfill_argv.append("--direct-nba")
            sys.argv = backfill_argv
            try:
                backfill_main()
            finally:
                sys.argv = original_argv

            run.status = "completed"
            run.finished_at = datetime.now(tz=timezone.utc)
            session.commit()

        except Exception as exc:
            run.status = "failed"
            run.finished_at = datetime.now(tz=timezone.utc)
            run.error_details = str(exc)
            session.commit()
            raise


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Daily NBA pipeline update")
    parser.add_argument("--start-date", type=_parse_date, required=True)
    parser.add_argument("--end-date", type=_parse_date, default=None)
    parser.add_argument(
        "--registered-only",
        action="store_true",
        default=False,
        help="Resume ingestion entirely from previously registered NBA reports (skip archive index discovery).",
    )
    args = parser.parse_args()
    if args.end_date is None:
        args.end_date = args.start_date

    run_daily_update(
        args.start_date,
        args.end_date,
        registered_only=args.registered_only,
    )


if __name__ == "__main__":
    main()
