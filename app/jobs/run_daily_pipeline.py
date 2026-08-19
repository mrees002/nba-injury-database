"""Two-step daily pipeline: sync schedule then update injuries.

Steps:
  1. Sync the auto-detected current NBA season schedule via the stats API.
  2. Run the daily injury updater for yesterday.

Exit non-zero on any failure.  Exceptions are not swallowed.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from app.db.session import build_engine, build_session_factory
from app.jobs.update_nba_daily import run_daily_update
from app.services.fetch_nba_schedule_api import (
    SEASON_TYPES_API,
    detect_current_season,
    fetch_season_schedule,
    normalized_games_to_rows,
)
from app.jobs.sync_nba_schedule import upsert_schedule_rows

logger = logging.getLogger(__name__)


def _run_schedule_sync() -> None:
    """Auto-detect season and upsert the live schedule."""
    season = detect_current_season()
    logger.info("Syncing live schedule for season %s", season)

    games = fetch_season_schedule(season, list(SEASON_TYPES_API))
    rows = normalized_games_to_rows(games)
    engine = build_engine()
    try:
        session_factory = build_session_factory(engine)
        with session_factory() as session:
            result = upsert_schedule_rows(session, rows, source="nba_stats_api")
    finally:
        engine.dispose()

    logger.info(
        "Schedule sync complete: fetched=%d upserted=%d skipped=%d",
        len(rows),
        result.upserted,
        result.skipped,
    )


def _run_daily_updater(target_date: date) -> None:
    """Run the daily injury backfill for *target_date*.

    Delegates to ``update_nba_daily.run_daily_update`` so that UpdateRun
    bookkeeping lives in one place.
    """
    logger.info("Running daily injury updater for %s", target_date.isoformat())
    run_daily_update(target_date, target_date)
    logger.info("Daily injury updater complete for %s", target_date.isoformat())


def main() -> int:
    """Run the two-step daily pipeline.

    Returns 0 on success, non-zero on failure.
    """
    yesterday = date.today() - timedelta(days=1)

    logger.info("=== Daily pipeline start ===")

    try:
        logger.info("Step 1/2: schedule sync")
        _run_schedule_sync()
    except Exception:
        logger.exception("Pipeline failed at step 1 (schedule sync)")
        return 1

    try:
        logger.info("Step 2/2: daily injury updater")
        _run_daily_updater(yesterday)
    except Exception:
        logger.exception("Pipeline failed at step 2 (daily injury updater)")
        return 1

    logger.info("=== Daily pipeline success ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
