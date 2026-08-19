"""Publish canonical NBAReportEntry rows to PublicInjuryEntry.

Idempotent: re-running replaces existing rows (same source_url + row_number).
Default publishes entries with condition_index=1 (primary condition) from 2019-20 onward.

Use --season 2018-19 to restore the partial 2018-19 window (2018-12-20 through
2019-10-21) without changing the default cutoff.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.db.session import build_engine, build_session_factory
from app.models.nba import (
    NBAInjuryCondition,
    NBAPlayer,
    NBAReport,
    NBAReportEntry,
    NBAScheduleGame,
    NBATeam,
    PublicInjuryEntry,
)

logger = logging.getLogger(__name__)

# 2019-20 season starts 2019-10-22.  Exclude everything before.
_SEASON_CUTOFF = date(2019, 10, 22)

# Partial 2018-19 restoration window (inclusive).
_PARTIAL_2018_19_START = date(2018, 12, 20)
_PARTIAL_2018_19_END = date(2019, 10, 21)  # day before _SEASON_CUTOFF


@dataclass
class PublishResult:
    inserted: int
    updated: int
    skipped: int
    total_canonical: int


def _build_publish_query(
    session: Session,
    *,
    min_date: date | None = None,
    max_date: date | None = None,
    season_filter: str | None = None,
):
    """Return a query that mirrors the current public API semantics.

    Joins:
      - NBAInjuryCondition where condition_index=1 (primary condition)
      - NBAPlayer for canonical player name
      - NBATeam for canonical team name
      - NBAReport for source_url, report_date, report_time
      - NBAScheduleGame for season/season_type (outer)

    Parameters:
      min_date: Inclusive lower bound for game_date. Defaults to _SEASON_CUTOFF.
      max_date: Inclusive upper bound for game_date. None means no upper bound.
      season_filter: If set, only include rows whose resolved season matches this value.
    """
    schedule = NBAScheduleGame.__table__
    query = (
        session.query(
            NBAReportEntry,
            NBAPlayer.canonical_name,
            NBATeam.canonical_name,
            NBAInjuryCondition.body_part,
            NBAInjuryCondition.injury_type,
            NBAReport.source_url,
            NBAReport.report_date,
            NBAReport.report_time,
            schedule.c.season,
            schedule.c.season_type,
        )
        .join(NBAPlayer, NBAReportEntry.player_id == NBAPlayer.id)
        .outerjoin(NBATeam, NBAReportEntry.team_id == NBATeam.id)
        .join(NBAReport, NBAReportEntry.report_id == NBAReport.id)
        .join(
            NBAInjuryCondition,
            and_(
                NBAInjuryCondition.report_entry_id == NBAReportEntry.id,
                NBAInjuryCondition.condition_index == 1,
            ),
        )
        .outerjoin(
            schedule,
            and_(
                schedule.c.game_date == NBAReportEntry.game_date,
                func.replace(schedule.c.matchup, " ", "")
                == func.replace(NBAReportEntry.matchup, " ", ""),
            ),
        )
        .filter(NBAReportEntry.game_date >= (min_date or _SEASON_CUTOFF))
    )
    if max_date is not None:
        query = query.filter(NBAReportEntry.game_date <= max_date)
    if season_filter is not None:
        query = query.filter(schedule.c.season == season_filter)
    return query


def publish_public_entries(
    session: Session,
    *,
    dry_run: bool = False,
    min_date: date | None = None,
    max_date: date | None = None,
    season_filter: str | None = None,
) -> PublishResult:
    """Populate PublicInjuryEntry from canonical archival tables.

    When dry_run=True, counts without modifying data.

    Parameters:
      min_date: Inclusive lower bound for game_date. Defaults to _SEASON_CUTOFF.
      max_date: Inclusive upper bound for game_date. None means no upper bound.
      season_filter: If set, only include rows whose resolved season matches.
    """
    rows = _build_publish_query(
        session,
        min_date=min_date,
        max_date=max_date,
        season_filter=season_filter,
    ).all()

    total_canonical = len(rows)
    inserted = 0
    updated = 0
    skipped = 0

    for (
        entry,
        player_name,
        team_name,
        body_part,
        injury_type,
        source_url,
        report_date,
        report_time,
        season,
        season_type,
    ) in rows:
        if dry_run:
            inserted += 1
            continue

        existing = (
            session.query(PublicInjuryEntry)
            .filter(
                PublicInjuryEntry.source_url == source_url,
                PublicInjuryEntry.row_number == entry.row_number,
            )
            .first()
        )

        if existing:
            existing.game_date = entry.game_date
            existing.game_time = entry.game_time
            existing.matchup = entry.matchup
            existing.player_id = entry.player_id
            existing.player_name = player_name
            existing.team_id = entry.team_id
            existing.team_name = team_name
            existing.status = entry.status
            existing.raw_reason = entry.raw_reason
            existing.reason_category = entry.reason_category
            existing.body_part = body_part
            existing.injury_type = injury_type
            existing.season = season
            existing.season_type = season_type
            updated += 1
        else:
            pub = PublicInjuryEntry(
                source_url=source_url,
                source_report_date=report_date,
                source_report_time=report_time,
                row_number=entry.row_number,
                game_date=entry.game_date,
                game_time=entry.game_time,
                matchup=entry.matchup,
                player_id=entry.player_id,
                player_name=player_name,
                team_id=entry.team_id,
                team_name=team_name,
                status=entry.status,
                raw_reason=entry.raw_reason,
                reason_category=entry.reason_category,
                body_part=body_part,
                injury_type=injury_type,
                season=season,
                season_type=season_type,
            )
            session.add(pub)
            inserted += 1

    if not dry_run:
        session.flush()

    return PublishResult(
        inserted=inserted,
        updated=updated,
        skipped=skipped,
        total_canonical=total_canonical,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish canonical NBAReportEntry rows to PublicInjuryEntry"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows without modifying data",
    )
    parser.add_argument(
        "--season",
        type=str,
        default=None,
        help=(
            "Restore a specific season without republishing the entire table. "
            "Currently supports '2018-19' (partial window 2018-12-20 through 2019-10-21)."
        ),
    )
    args = parser.parse_args()

    # Resolve season-specific parameters.
    min_date: date | None = None
    max_date: date | None = None
    season_filter: str | None = None
    if args.season == "2018-19":
        min_date = _PARTIAL_2018_19_START
        max_date = _PARTIAL_2018_19_END
        season_filter = "2018-19"
    elif args.season is not None:
        parser.error(
            f"Unsupported season '{args.season}'. Currently supported: '2018-19'"
        )

    session_factory = build_session_factory(build_engine())
    with session_factory() as session:
        result = publish_public_entries(
            session,
            dry_run=args.dry_run,
            min_date=min_date,
            max_date=max_date,
            season_filter=season_filter,
        )
        if not args.dry_run:
            session.commit()

    mode = "dry-run" if args.dry_run else "published"
    season_label = f" season={args.season}" if args.season else ""
    print(
        f"{mode}{season_label}: canonical={result.total_canonical} "
        f"inserted={result.inserted} updated={result.updated} "
        f"skipped={result.skipped}"
    )


if __name__ == "__main__":
    main()
