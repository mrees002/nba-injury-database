from __future__ import annotations

from datetime import date, time

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import NBAInjuryCondition, NBAReport, NBAReportCandidate, NBAReportEntry, NBATeam
from app.nba.parser import PARSER_VERSION
from app.nba.reparse import reparse_reports


def test_offline_reparse_rebuilds_entries_and_preserves_report_lineage(nba_pdf_loader):
    content = nba_pdf_loader("legacy_structural_defects_v1")
    source_url = "https://ak-static.cms.nba.com/referee/injury/fixture-stored.pdf"
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        candidate = NBAReportCandidate(
            source_url=source_url,
            report_date=date(2018, 12, 23),
            report_time=time(13, 30),
            discovery_source_url="fixture:stored-pdf",
            status="parsed",
            attempt_count=1,
        )
        session.add(candidate)
        session.flush()
        report = NBAReport(
            candidate_id=candidate.id,
            report_date=date(2018, 12, 23),
            report_time=time(13, 30),
            source_url=source_url,
            content_hash="a" * 64,
            content=content,
            content_type="application/pdf",
            byte_length=len(content),
            parser_version="nba-pdf-v4",
            format_version="legacy-category-v1",
            parse_status="parsed",
            raw_text="old parser output",
        )
        session.add(report)
        session.flush()
        candidate.resolved_report_id = report.id
        stale_team = NBATeam(canonical_name="Minnesota")
        session.add(stale_team)
        session.flush()
        stale_team_id = stale_team.id
        session.add(
            NBAReportEntry(
                report_id=report.id,
                page_number=1,
                row_number=1,
                team_id=stale_team_id,
                entry_type="player",
                game_date=date(2018, 12, 23),
                game_time=time(19),
                matchup="SAS@PHX",
                team_name_raw="old team",
                player_name_raw="old player",
                status="Out",
                raw_row_text="old parser row",
            )
        )
        session.commit()

        first = reparse_reports(session, date(2018, 12, 23), date(2018, 12, 23))
        second = reparse_reports(session, date(2018, 12, 23), date(2018, 12, 23))

        session.refresh(report)
        session.refresh(candidate)
        entries = list(
            session.scalars(
                select(NBAReportEntry)
                .where(NBAReportEntry.report_id == report.id)
                .order_by(NBAReportEntry.row_number)
            )
        )
        assert first.selected == first.parsed == 1
        assert first.failed == 0
        assert first.entries_inserted == 6
        assert second.selected == second.parsed == second.failed == 0
        assert report.parser_version == PARSER_VERSION
        assert report.content == content
        assert report.source_url == source_url
        assert candidate.resolved_report_id == report.id
        assert candidate.status == "parsed"
        assert len(entries) == 6
        assert sum(entry.entry_type == "all_available" for entry in entries) == 2
        assert not any(entry.team_name_raw in {"Minnesota", "Timberwolves"} for entry in entries)
        assert session.get(NBATeam, stale_team_id) is None
        assert (session.scalar(select(func.count()).select_from(NBAInjuryCondition)) or 0) == 3
