"""Publish canonical NBAReportEntry rows to PublicInjuryEntry.

Idempotent: re-running replaces existing rows (same source_url + row_number).
Only publishes entries with condition_index=1 (primary condition) from 2019-20 onward.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import date

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


@dataclass
class PublishResult:
    inserted: int
    updated: int
    skipped: int
    total_canonical: int


def _build_publish_query(session: Session):
    """Return a query that mirrors the current public API semantics.

    Joins:
      - NBAInjuryCondition where condition_index=1 (primary condition)
      - NBAPlayer for canonical player name
      - NBATeam for canonical team name
      - NBAReport for source_url, report_date, report_time
      - NBAScheduleGame for season/season_type (outer)
    """
    schedule = NBAScheduleGame.__table__
    return (
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
        .filter(NBAReportEntry.game_date >= _SEASON_CUTOFF)
    )


def publish_public_entries(session: Session, *, dry_run: bool = False) -> PublishResult:
    """Populate PublicInjuryEntry from canonical archival tables.

    When dry_run=True, counts without modifying data.
    """
    rows = _build_publish_query(session).all()

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
    args = parser.parse_args()

    session_factory = build_session_factory(build_engine())
    with session_factory() as session:
        result = publish_public_entries(session, dry_run=args.dry_run)
        if not args.dry_run:
            session.commit()

    mode = "dry-run" if args.dry_run else "published"
    print(
        f"{mode}: canonical={result.total_canonical} "
        f"inserted={result.inserted} updated={result.updated} "
        f"skipped={result.skipped}"
    )


if __name__ == "__main__":
    main()
