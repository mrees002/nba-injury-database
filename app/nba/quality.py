from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.models import (
    NBAGame,
    NBAInjuryCondition,
    NBAPlayer,
    NBAReport,
    NBAReportCandidate,
    NBAReportEntry,
    NBATeam,
)
from app.nba.seasons import get_official_nba_season


def _count_map(rows: list[tuple[str | None, int]]) -> dict[str, int]:
    return {key or "<null>": count for key, count in rows}


def build_quality_report(session: Session) -> dict[str, object]:
    report_dates = session.execute(
        select(func.min(NBAReport.report_date), func.max(NBAReport.report_date))
    ).one()
    reports = session.scalar(select(func.count()).select_from(NBAReport)) or 0
    parsed_reports = (
        session.scalar(
            select(func.count()).select_from(NBAReport).where(NBAReport.parse_status == "parsed")
        )
        or 0
    )
    entries = session.scalar(select(func.count()).select_from(NBAReportEntry)) or 0
    conditions = session.scalar(select(func.count()).select_from(NBAInjuryCondition)) or 0
    injury_conditions = (
        session.scalar(
            select(func.count())
            .select_from(NBAInjuryCondition)
            .where(NBAInjuryCondition.is_injury.is_(True))
        )
        or 0
    )
    body_classified = (
        session.scalar(
            select(func.count())
            .select_from(NBAInjuryCondition)
            .where(
                NBAInjuryCondition.is_injury.is_(True),
                NBAInjuryCondition.body_part.is_not(None),
            )
        )
        or 0
    )
    type_classified = (
        session.scalar(
            select(func.count())
            .select_from(NBAInjuryCondition)
            .where(
                NBAInjuryCondition.is_injury.is_(True),
                NBAInjuryCondition.injury_type.is_not(None),
            )
        )
        or 0
    )
    laterality = (
        session.scalar(
            select(func.count())
            .select_from(NBAInjuryCondition)
            .where(
                NBAInjuryCondition.is_injury.is_(True),
                NBAInjuryCondition.laterality.is_not(None),
            )
        )
        or 0
    )
    season_counts: Counter[str] = Counter()
    entry_season_counts: Counter[str] = Counter()
    reports_by_date: dict[str, int] = {}
    dates_by_season: dict[str, set[date]] = {}
    for report_date, count in session.execute(
        select(NBAReport.report_date, func.count()).group_by(NBAReport.report_date)
    ):
        season = get_official_nba_season(report_date)
        season_counts[season] += count
        reports_by_date[report_date.isoformat()] = count
        dates_by_season.setdefault(season, set()).add(report_date)
    for report_date, count in session.execute(
        select(NBAReport.report_date, func.count())
        .join(NBAReportEntry, NBAReportEntry.report_id == NBAReport.id)
        .group_by(NBAReport.report_date)
    ):
        entry_season_counts[get_official_nba_season(report_date)] += count

    missing_body = (
        session.scalar(
            select(func.count())
            .select_from(NBAInjuryCondition)
            .where(
                NBAInjuryCondition.is_injury.is_(True),
                NBAInjuryCondition.body_part.is_(None),
            )
        )
        or 0
    )
    missing_type = (
        session.scalar(
            select(func.count())
            .select_from(NBAInjuryCondition)
            .where(
                NBAInjuryCondition.is_injury.is_(True),
                NBAInjuryCondition.injury_type.is_(None),
            )
        )
        or 0
    )
    fully_unclassified = (
        session.scalar(
            select(func.count())
            .select_from(NBAInjuryCondition)
            .where(
                NBAInjuryCondition.is_injury.is_(True),
                NBAInjuryCondition.body_part.is_(None),
                NBAInjuryCondition.injury_type.is_(None),
            )
        )
        or 0
    )
    common_unclassified = [
        {"reason": reason or "<null>", "observations": count}
        for reason, count in session.execute(
            select(NBAReportEntry.raw_reason, func.count())
            .join(
                NBAInjuryCondition,
                NBAInjuryCondition.report_entry_id == NBAReportEntry.id,
            )
            .where(
                NBAInjuryCondition.is_injury.is_(True),
                NBAInjuryCondition.body_part.is_(None),
                NBAInjuryCondition.injury_type.is_(None),
            )
            .group_by(NBAReportEntry.raw_reason)
            .order_by(func.count().desc(), NBAReportEntry.raw_reason)
            .limit(50)
        )
    ]
    conditions_per_entry = (
        select(
            NBAInjuryCondition.report_entry_id.label("entry_id"),
            func.count().label("condition_count"),
        )
        .group_by(NBAInjuryCondition.report_entry_id)
        .subquery()
    )
    multiple_condition_entries = (
        session.scalar(
            select(func.count())
            .select_from(conditions_per_entry)
            .where(conditions_per_entry.c.condition_count > 1)
        )
        or 0
    )
    coverage_gaps: dict[str, dict[str, object]] = {}
    for season, represented in sorted(dates_by_season.items()):
        first = min(represented)
        last = max(represented)
        span = (last - first).days + 1
        missing_dates = [
            (first + timedelta(days=offset)).isoformat()
            for offset in range(span)
            if first + timedelta(days=offset) not in represented
        ]
        coverage_gaps[season] = {
            "first_report_date": first.isoformat(),
            "last_report_date": last.isoformat(),
            "represented_dates": len(represented),
            "unrepresented_calendar_dates_within_span": len(missing_dates),
            "unrepresented_dates": missing_dates,
            "note": "Includes legitimate off-days; no authoritative expected-report index exists.",
        }

    return {
        "earliest_report_date": report_dates[0].isoformat() if report_dates[0] else None,
        "latest_report_date": report_dates[1].isoformat() if report_dates[1] else None,
        "reports": reports,
        "reports_by_season": dict(sorted(season_counts.items())),
        "entries_by_season": dict(sorted(entry_season_counts.items())),
        "reports_by_date": dict(sorted(reports_by_date.items())),
        "date_coverage_gaps": coverage_gaps,
        "candidate_statuses": _count_map(
            list(
                session.execute(
                    select(NBAReportCandidate.status, func.count())
                    .group_by(NBAReportCandidate.status)
                    .order_by(NBAReportCandidate.status)
                )
            )
        ),
        "candidate_urls_with_resolved_report": session.scalar(
            select(func.count())
            .select_from(NBAReportCandidate)
            .where(NBAReportCandidate.resolved_report_id.is_not(None))
        )
        or 0,
        "candidate_url_aliases_of_identical_content": session.scalar(
            select(func.count())
            .select_from(NBAReportCandidate)
            .join(NBAReport, NBAReport.id == NBAReportCandidate.resolved_report_id)
            .where(NBAReport.candidate_id != NBAReportCandidate.id)
        )
        or 0,
        "parse_success_percentage": round(parsed_reports * 100 / reports, 3) if reports else 0,
        "entries": entries,
        "classified_observations": conditions,
        "entries_with_multiple_conditions": multiple_condition_entries,
        "injury_observations": injury_conditions,
        "unique_players": session.scalar(select(func.count(distinct(NBAPlayer.id)))) or 0,
        "unique_teams": session.scalar(select(func.count(distinct(NBATeam.id)))) or 0,
        "unique_scheduled_games": session.scalar(select(func.count(distinct(NBAGame.id)))) or 0,
        "entries_with_previous_status": session.scalar(
            select(func.count())
            .select_from(NBAReportEntry)
            .where(NBAReportEntry.previous_status.is_not(None))
        )
        or 0,
        "entries_with_previous_reason": session.scalar(
            select(func.count())
            .select_from(NBAReportEntry)
            .where(NBAReportEntry.previous_reason.is_not(None))
        )
        or 0,
        "entries_by_status": _count_map(
            list(
                session.execute(
                    select(NBAReportEntry.status, func.count())
                    .group_by(NBAReportEntry.status)
                    .order_by(NBAReportEntry.status)
                )
            )
        ),
        "entries_by_reason_category": _count_map(
            list(
                session.execute(
                    select(NBAReportEntry.reason_category, func.count())
                    .group_by(NBAReportEntry.reason_category)
                    .order_by(NBAReportEntry.reason_category)
                )
            )
        ),
        "conditions_by_classification_version": _count_map(
            list(
                session.execute(
                    select(NBAInjuryCondition.classification_version, func.count())
                    .group_by(NBAInjuryCondition.classification_version)
                    .order_by(NBAInjuryCondition.classification_version)
                )
            )
        ),
        "format_versions": _count_map(
            list(
                session.execute(
                    select(NBAReport.format_version, func.count())
                    .group_by(NBAReport.format_version)
                    .order_by(NBAReport.format_version)
                )
            )
        ),
        "body_part_classification_percentage": round(body_classified * 100 / injury_conditions, 3)
        if injury_conditions
        else 0,
        "injury_type_classification_percentage": round(type_classified * 100 / injury_conditions, 3)
        if injury_conditions
        else 0,
        "laterality_percentage": round(laterality * 100 / injury_conditions, 3)
        if injury_conditions
        else 0,
        "classification_gaps": {
            "missing_body_part": missing_body,
            "missing_injury_type": missing_type,
            "missing_both": fully_unclassified,
            "most_common_fully_unclassified_reasons": common_unclassified,
        },
        "generated_through": date.today().isoformat(),
    }
