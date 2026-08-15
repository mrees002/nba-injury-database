from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from app.models import (
    NBAGame,
    NBAInjuryEpisode,
    NBAReport,
    NBAReportCandidate,
    NBAReportEntry,
    NBATeam,
)
from app.nba.parser import PARSER_VERSION, parse_report_pdf
from app.nba.repository import EntityResolver, persist_parsed_report


@dataclass(frozen=True)
class ReparseResult:
    selected: int
    parsed: int
    failed: int
    entries_inserted: int


def reparse_reports(session: Session, start_date: date, end_date: date) -> ReparseResult:
    episode_count = session.scalar(select(func.count()).select_from(NBAInjuryEpisode)) or 0
    if episode_count:
        raise RuntimeError(
            "Reparsing would invalidate derived episodes; clear/rebuild episodes in the same "
            "controlled workflow first"
        )
    criteria = (
        NBAReport.report_date >= start_date,
        NBAReport.report_date <= end_date,
        (NBAReport.parser_version != PARSER_VERSION)
        | NBAReport.parser_version.is_(None)
        | (NBAReport.parse_status == "failed"),
    )
    report_ids = list(
        session.scalars(
            select(NBAReport.id)
            .where(*criteria)
            .order_by(NBAReport.report_date, NBAReport.report_time)
        )
    )
    selected_count = len(report_ids)
    parsed_count = 0
    failed = 0
    entries = 0
    resolver = EntityResolver(session)
    for report_id in report_ids:
        selected_report = session.get(NBAReport, report_id)
        if selected_report is None:
            continue
        candidate_id = selected_report.candidate_id
        try:
            parsed = parse_report_pdf(
                selected_report.content, source_url=selected_report.source_url
            )
            entries += persist_parsed_report(session, selected_report, parsed, resolver=resolver)
            candidate = session.get(NBAReportCandidate, candidate_id)
            if candidate:
                candidate.status = "parsed"
                candidate.last_error = None
            session.commit()
            parsed_count += 1
        except Exception as exc:
            session.rollback()
            selected_report = session.get(NBAReport, report_id)
            candidate = session.get(NBAReportCandidate, candidate_id)
            if selected_report:
                selected_report.parse_status = "failed"
                selected_report.parse_error = f"{type(exc).__name__}: {exc}"
            if candidate:
                candidate.status = "parse_failed"
                candidate.last_error = f"{type(exc).__name__}: {exc}"
            session.commit()
            resolver = EntityResolver(session)
            failed += 1
    session.execute(
        delete(NBATeam)
        .where(
            ~exists(select(NBAReportEntry.id).where(NBAReportEntry.team_id == NBATeam.id)),
            ~exists(select(NBAGame.id).where(NBAGame.away_team_id == NBATeam.id)),
            ~exists(select(NBAGame.id).where(NBAGame.home_team_id == NBATeam.id)),
        )
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return ReparseResult(
        selected=selected_count,
        parsed=parsed_count,
        failed=failed,
        entries_inserted=entries,
    )
