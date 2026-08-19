"""Sync NBA schedule games into the database.

Supports three modes:
  1. CSV import: load a local schedule CSV file (idempotent).
  2. Current-season upsert: merge a list of schedule rows from stdin JSON.
  3. Live sync: fetch from the NBA stats API and upsert into the DB.

Usage:
    python -m app.jobs.sync_nba_schedule import-csv data/reference/nba_schedule_games.csv
    python -m app.jobs.sync_nba_schedule upsert --season 2025-26
    python -m app.jobs.sync_nba_schedule sync-live
    python -m app.jobs.sync_nba_schedule sync-live --season 2024-25
    python -m app.jobs.sync_nba_schedule sync-live --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import build_engine, build_session_factory
from app.models.nba import NBAScheduleGame
from app.services.schedule_import import (
    ScheduleCSVValidationError,
    ScheduleImportResult,
    import_schedule_csv,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpsertResult:
    upserted: int
    skipped: int


def upsert_schedule_rows(
    session: Session,
    rows: list[dict],
    source: str | None = None,
) -> UpsertResult:
    """Upsert schedule rows into nba_schedule_games.

    Each row dict must contain: season, game_date, season_type, away_team, home_team, matchup.
    Existing rows (matched by season+game_date+matchup) are updated in place;
    new rows are inserted.  Returns counts of upserted and skipped rows.
    """
    upserted = 0
    skipped = 0

    for row in rows:
        season = row["season"].strip()
        game_date = row["game_date"]
        if isinstance(game_date, str):
            game_date = date.fromisoformat(game_date)
        season_type = row["season_type"].strip()
        away_team = row["away_team"].strip()
        home_team = row["home_team"].strip()
        matchup = row["matchup"].strip()

        existing = session.execute(
            select(NBAScheduleGame).where(
                NBAScheduleGame.season == season,
                NBAScheduleGame.game_date == game_date,
                NBAScheduleGame.matchup == matchup,
            )
        ).scalar_one_or_none()

        if existing is not None:
            changed = False
            for field, value in [
                ("season_type", season_type),
                ("away_team", away_team),
                ("home_team", home_team),
                ("source", source),
            ]:
                if getattr(existing, field) != value:
                    setattr(existing, field, value)
                    changed = True
            if changed:
                upserted += 1
            else:
                skipped += 1
        else:
            session.add(
                NBAScheduleGame(
                    season=season,
                    game_date=game_date,
                    season_type=season_type,
                    away_team=away_team,
                    home_team=home_team,
                    matchup=matchup,
                    source=source,
                )
            )
            upserted += 1

    session.commit()
    return UpsertResult(upserted=upserted, skipped=skipped)


def _build_import_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = subparsers.add_parser("import-csv", help="Import a schedule CSV file")
    parser.add_argument("path", type=str, help="Path to schedule CSV file")
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Source label to tag imported rows",
    )


def _build_upsert_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = subparsers.add_parser("upsert", help="Upsert current-season schedule rows from stdin")
    parser.add_argument(
        "--season",
        required=True,
        type=str,
        help="Season label, e.g. 2025-26",
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Source label to tag upserted rows",
    )


def _build_sync_live_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = subparsers.add_parser(
        "sync-live",
        help="Fetch schedule from the NBA stats API and upsert",
    )
    parser.add_argument(
        "--season",
        type=str,
        default=None,
        help="Season label, e.g. 2025-26 (auto-detected when omitted)",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="nba_stats_api",
        help="Source label to tag upserted rows (default: nba_stats_api)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Fetch and normalize but do not write to the database",
    )
    parser.add_argument(
        "--season-type",
        action="append",
        dest="season_types",
        default=None,
        help=(
            "API season type to fetch (may be repeated). "
            "Default: all four types (Pre Season, Regular Season, PlayIn, Playoffs). "
            "Valid values: Pre Season, Regular Season, PlayIn, Playoffs"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP request timeout in seconds (default: 30)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync NBA schedule games")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _build_import_parser(subparsers)
    _build_upsert_parser(subparsers)
    _build_sync_live_parser(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    engine = None

    try:
        engine = build_engine()
        session_factory = build_session_factory(engine)

        with session_factory() as session:
            if args.command == "import-csv":
                result = _run_import_csv(session, args)
                print(
                    f"read={result.read} inserted={result.inserted} "
                    f"skipped={result.skipped} invalid={result.invalid}"
                )
            elif args.command == "upsert":
                result = _run_upsert(session, args)
                print(f"upserted={result.upserted} skipped={result.skipped}")
            elif args.command == "sync-live":
                result = _run_sync_live(session, args)
                print(
                    f"fetched={result.fetched} upserted={result.upserted} "
                    f"skipped={result.skipped}"
                )
            else:
                print(f"Unknown command: {args.command}", file=sys.stderr)
                return 1
    except (ScheduleCSVValidationError, OSError, ValueError, RuntimeError) as exc:
        print(f"Sync failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()

    return 0


def _run_import_csv(session: Session, args: argparse.Namespace) -> ScheduleImportResult:
    return import_schedule_csv(session, args.path, source=args.source)


def _run_upsert(session: Session, args: argparse.Namespace) -> UpsertResult:
    import json
    import sys

    data = json.load(sys.stdin)
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of schedule row objects")
    return upsert_schedule_rows(session, data, source=args.source)


@dataclass(frozen=True)
class SyncLiveResult:
    fetched: int
    upserted: int
    skipped: int


def _run_sync_live(session: Session, args: argparse.Namespace) -> SyncLiveResult:
    # Lazy import to avoid pulling in httpx at module load for modes that
    # don't need network access.
    from app.services import fetch_nba_schedule_api as api

    season = args.season or api.detect_current_season()
    season_types = args.season_types or list(api.SEASON_TYPES_API)

    logger.info("Syncing live schedule for %s (types: %s)", season, season_types)

    games = api.fetch_season_schedule(
        season,
        season_types,
        timeout=args.timeout,
    )
    rows = api.normalized_games_to_rows(games)

    if args.dry_run:
        print(f"[dry-run] Would upsert {len(rows)} rows for {season}")
        for row in rows[:5]:
            print(
                f"  {row['game_date']} {row['season_type']:>10s} "
                f"{row['matchup']}"
            )
        if len(rows) > 5:
            print(f"  ... and {len(rows) - 5} more")
        return SyncLiveResult(fetched=len(rows), upserted=0, skipped=0)

    result = upsert_schedule_rows(session, rows, source=args.source)
    return SyncLiveResult(
        fetched=len(rows),
        upserted=result.upserted,
        skipped=result.skipped,
    )


if __name__ == "__main__":
    raise SystemExit(main())
