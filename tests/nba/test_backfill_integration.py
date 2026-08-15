from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    NBAInjuryCondition,
    NBAInjuryEpisode,
    NBAInjuryEpisodeCondition,
    NBAPlayer,
    NBAReport,
    NBAReportCandidate,
    NBAReportEntry,
)
from app.nba.backfill import NBAReportBackfill
from app.nba.classification import CLASSIFICATION_VERSION
from app.nba.client import NBAReportClient
from app.nba.episode_audit import build_episode_audit
from app.nba.episodes import episode_semantic_digest, rebuild_injury_episodes
from app.nba.parser import PARSER_VERSION, Report, select_latest_reports
from app.nba.quality import build_quality_report
from app.nba.reclassify import reclassify_conditions
from app.nba.repository import persist_parsed_report
from app.nba.types import DiscoveredReport, ParsedNBAReport, ParsedNBAReportEntry


def test_backfill_and_episode_rebuild_are_idempotent(nba_pdf_builder):
    fixture = Path(__file__).parents[1] / "fixtures" / "nba_reports" / "compact_v3.json"
    content = nba_pdf_builder(json.loads(fixture.read_text(encoding="utf-8"))["pages"])
    source_url = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-04-12_05_00PM.pdf"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=content,
            request=request,
        )
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with (
        Session(engine) as session,
        NBAReportClient(
            user_agent="fixture-agent", request_interval_seconds=0, transport=transport
        ) as client,
    ):
        backfill = NBAReportBackfill(session, client)
        registration = backfill.register(
            [
                DiscoveredReport(
                    source_url=source_url,
                    report_date=date(2026, 4, 12),
                    report_time=time(17),
                    discovery_source_url="fixture:index",
                )
            ]
        )
        first = backfill.run(date(2026, 4, 12), date(2026, 4, 12))
        second = backfill.run(date(2026, 4, 12), date(2026, 4, 12))

        # Characterize recovery from an interrupted/concurrent run where the durable report
        # committed but its candidate status did not.
        candidate = session.scalar(select(NBAReportCandidate))
        candidate.status = "parse_failed"
        candidate.last_error = "interrupted concurrent worker"
        session.commit()
        reconciled = backfill.run(date(2026, 4, 12), date(2026, 4, 12))

        assert registration.candidates_inserted == 1
        assert (first.downloaded, first.parsed, first.entries_inserted) == (1, 1, 4)
        assert (second.downloaded, second.parsed, second.entries_inserted) == (0, 0, 0)
        assert (reconciled.downloaded, reconciled.parsed, reconciled.already_known) == (0, 0, 1)
        assert candidate.status == "parsed"
        assert session.scalar(select(func.count()).select_from(NBAReportCandidate)) == 1
        assert session.scalar(select(func.count()).select_from(NBAReport)) == 1
        assert session.scalar(select(func.count()).select_from(NBAReportEntry)) == 4
        assert session.scalar(select(func.count()).select_from(NBAPlayer)) == 4

        quality = build_quality_report(session)
        assert quality["reports"] == 1
        assert quality["entries"] == 4
        assert quality["entries_by_season"] == {"2025-26": 4}
        assert quality["injury_observations"] == 3
        assert quality["entries_with_multiple_conditions"] == 0
        assert quality["conditions_by_classification_version"] == {CLASSIFICATION_VERSION: 4}
        assert quality["format_versions"] == {"compact-v3": 1}
        assert quality["parse_success_percentage"] == 100
        assert quality["classification_gaps"] == {
            "missing_body_part": 0,
            "missing_injury_type": 0,
            "missing_both": 0,
            "most_common_fully_unclassified_reasons": [],
        }

        condition = session.scalar(select(NBAInjuryCondition).limit(1))
        condition.classification_version = "old-version"
        session.commit()
        reclassified = reclassify_conditions(session)
        assert reclassified.selected == 1
        assert condition.classification_version == CLASSIFICATION_VERSION

        episode_first = rebuild_injury_episodes(session)
        first_digest = episode_semantic_digest(session)
        snapshot = list(
            session.execute(
                select(
                    NBAInjuryEpisode.player_id,
                    NBAInjuryEpisode.start_date,
                    NBAInjuryEpisode.body_part,
                    NBAInjuryEpisode.laterality,
                    NBAInjuryEpisode.injury_type,
                ).order_by(NBAInjuryEpisode.player_id)
            )
        )
        episode_second = rebuild_injury_episodes(session)
        second_digest = episode_semantic_digest(session)
        episode_audit = build_episode_audit(session)
        repeated_snapshot = list(
            session.execute(
                select(
                    NBAInjuryEpisode.player_id,
                    NBAInjuryEpisode.start_date,
                    NBAInjuryEpisode.body_part,
                    NBAInjuryEpisode.laterality,
                    NBAInjuryEpisode.injury_type,
                ).order_by(NBAInjuryEpisode.player_id)
            )
        )

        assert episode_first == episode_second
        assert first_digest == second_digest
        assert episode_first.episodes == 3
        assert episode_audit["episodes"] == 3
        assert episode_audit["linked_injury_conditions"] == 3
        assert episode_audit["unique_linked_injury_conditions"] == 3
        assert episode_audit["unlinked_injury_conditions"] == 0
        assert episode_audit["episodes_without_lineage"] == 0
        assert episode_audit["episodes_with_cross_team_lineage"] == 0
        assert snapshot == repeated_snapshot


def test_content_hash_deduplicates_same_pdf_at_two_urls(nba_pdf_loader):
    content = nba_pdf_loader("compact_v3")
    prefix = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-04-12_"
    urls = [f"{prefix}05_00PM.pdf", f"{prefix}05_15PM.pdf"]
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=content,
            request=request,
        )
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with (
        Session(engine) as session,
        NBAReportClient(
            user_agent="fixture-agent", request_interval_seconds=0, transport=transport
        ) as client,
    ):
        backfill = NBAReportBackfill(session, client)
        backfill.register(
            [
                DiscoveredReport(url, date(2026, 4, 12), time(17, index * 15), "fixture:index")
                for index, url in enumerate(urls)
            ]
        )
        result = backfill.run(date(2026, 4, 12), date(2026, 4, 12))

        assert result.downloaded == 2
        assert result.parsed == 1
        assert result.already_known == 1
        assert session.scalar(select(func.count()).select_from(NBAReportCandidate)) == 2
        assert session.scalar(select(func.count()).select_from(NBAReport)) == 1
        assert session.scalar(select(func.count()).select_from(NBAReportEntry)) == 4
        resolved_ids = list(
            session.scalars(
                select(NBAReportCandidate.resolved_report_id).order_by(NBAReportCandidate.id)
            )
        )
        assert resolved_ids[0] == resolved_ids[1]


def test_parse_failure_is_tracked_and_does_not_crash_backfill():
    source_url = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-04-12_05_00PM.pdf"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.4\nmalformed",
            request=request,
        )
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with (
        Session(engine) as session,
        NBAReportClient(
            user_agent="fixture-agent", request_interval_seconds=0, transport=transport
        ) as client,
    ):
        backfill = NBAReportBackfill(session, client)
        backfill.register(
            [DiscoveredReport(source_url, date(2026, 4, 12), time(17), "fixture:index")]
        )
        result = backfill.run(date(2026, 4, 12), date(2026, 4, 12))

        candidate = session.scalar(select(NBAReportCandidate))
        report = session.scalar(select(NBAReport))
        assert result.parse_failures == 1
        assert candidate.status == "parse_failed"
        assert report.parse_status == "failed"
        assert "Unreadable PDF" in candidate.last_error


def test_compound_reason_persists_separate_conditions_with_shared_lineage():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    raw_reason = "Injury/Illness - Left Hip; Strain, Right Hip; Soreness"
    source_url = "https://ak-static.cms.nba.com/referee/injury/compound.pdf"
    with Session(engine) as session:
        candidate = NBAReportCandidate(
            source_url=source_url,
            report_date=date(2026, 1, 2),
            report_time=time(17),
            status="downloaded",
            attempt_count=1,
        )
        session.add(candidate)
        session.flush()
        report = NBAReport(
            candidate_id=candidate.id,
            report_date=date(2026, 1, 2),
            report_time=time(17),
            source_url=source_url,
            content_hash="a" * 64,
            content=b"%PDF-fixture",
            content_type="application/pdf",
            byte_length=12,
            parse_status="pending",
        )
        session.add(report)
        session.flush()
        parsed = ParsedNBAReport(
            report_date=date(2026, 1, 2),
            report_time=time(17),
            format_version="fixture-v1",
            parser_version=PARSER_VERSION,
            raw_text="fixture",
            entries=(
                ParsedNBAReportEntry(
                    page_number=1,
                    row_number=1,
                    game_date=date(2026, 1, 2),
                    game_time=time(19),
                    matchup="BOS@CHI",
                    team="Boston Celtics",
                    player_name="Example, Player",
                    status="Out",
                    reason_category="Injury/Illness",
                    raw_reason=raw_reason,
                    previous_status=None,
                    previous_reason=None,
                    raw_row_text="source row",
                ),
            ),
        )

        assert persist_parsed_report(session, report, parsed) == 1
        session.commit()

        entry = session.scalar(select(NBAReportEntry))
        conditions = list(
            session.scalars(select(NBAInjuryCondition).order_by(NBAInjuryCondition.condition_index))
        )
        assert entry.raw_reason == raw_reason
        assert [(item.body_part, item.laterality, item.injury_type) for item in conditions] == [
            ("hip", "left", "strain"),
            ("hip", "right", "soreness"),
        ]
        assert {item.report_entry_id for item in conditions} == {entry.id}


def test_direct_nba_selects_only_latest_pdf_per_game(nba_pdf_builder):
    """Multiple direct-NBA PDFs for the same game result in only the latest being parsed."""
    earlier_page = [
        [285, 550, "Injury Report: 04/12/26 05:00 PM"],
        [24, 510, "Game Date"], [120, 510, "Game Time"], [200, 510, "Matchup"],
        [265, 510, "Team"], [426, 510, "Player Name"], [587, 510, "Current Status"],
        [667, 510, "Reason"],
        [24, 485, "04/12/2026"], [120, 485, "06:00 (ET)"], [200, 485, "BOS@MIL"],
        [265, 485, "Boston Celtics"], [426, 485, "Brown, Jaylen"],
        [587, 485, "Out"], [667, 485, "Injury/Illness - Right Ankle; Sprain"],
    ]
    later_page = [
        [285, 550, "Injury Report: 04/12/26 09:00 PM"],
        [24, 510, "Game Date"], [120, 510, "Game Time"], [200, 510, "Matchup"],
        [265, 510, "Team"], [426, 510, "Player Name"], [587, 510, "Current Status"],
        [667, 510, "Reason"],
        [24, 485, "04/12/2026"], [120, 485, "06:00 (ET)"], [200, 485, "BOS@MIL"],
        [265, 485, "Boston Celtics"], [426, 485, "Tatum, Jayson"],
        [587, 485, "Questionable"], [667, 485, "Injury/Illness - Left Knee; Soreness"],
    ]
    earlier_content = nba_pdf_builder([earlier_page])
    later_content = nba_pdf_builder([later_page])

    later_url = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-04-12_09_00PM.pdf"
    later_disc = DiscoveredReport(later_url, date(2026, 4, 12), time(21), "fixture:index")

    earlier_report = Report(content=earlier_content, timestamp=datetime(2026, 4, 12, 17, 0))
    later_report = Report(content=later_content, timestamp=datetime(2026, 4, 12, 21, 0))
    selected = select_latest_reports([earlier_report, later_report])
    assert len(selected) == 1
    assert selected[0] is later_report

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=later_content,
            request=request,
        )
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with (
        Session(engine) as session,
        NBAReportClient(
            user_agent="fixture-agent", request_interval_seconds=0, transport=transport
        ) as client,
    ):
        backfill = NBAReportBackfill(session, client)
        backfill.register([later_disc])
        result = backfill.run(date(2026, 4, 12), date(2026, 4, 12))

        assert result.parsed == 1
        assert result.entries_inserted == 1
        assert session.scalar(select(func.count()).select_from(NBAReport)) == 1
        assert session.scalar(select(func.count()).select_from(NBAReportEntry)) == 1


def test_direct_nba_multi_day_per_date_processing(nba_pdf_builder):
    """Multi-day direct NBA processes each date independently; earlier dates persist on failure."""
    from datetime import timedelta
    from io import BytesIO

    import pdfplumber

    from app.nba.discovery import (
        NBA_PDF_PREFIX,
        generate_candidate_urls,
        parse_report_url,
        probe_candidate_urls,
    )
    from app.nba.parser import _REPORT_TIMESTAMP_RE

    def make_page(report_date_str, player):
        return [
            [285, 550, f"Injury Report: {report_date_str} 05:00 PM"],
            [24, 510, "Game Date"], [120, 510, "Game Time"], [200, 510, "Matchup"],
            [265, 510, "Team"], [426, 510, "Player Name"], [587, 510, "Current Status"],
            [667, 510, "Reason"],
            [24, 485, "04/10/2026"], [120, 485, "06:00 (ET)"], [200, 485, "BOS@NYK"],
            [265, 485, "Boston Celtics"], [426, 485, player],
            [587, 485, "Out"], [667, 485, "Injury/Illness - Left Ankle; Sprain"],
        ]

    content_10 = nba_pdf_builder([make_page("04/10/26", "Tatum, Jayson")])
    content_11 = nba_pdf_builder([make_page("04/11/26", "Brown, Jaylen")])
    content_12 = nba_pdf_builder([make_page("04/12/26", "Antetokounmpo, Giannis")])

    prefix = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_"
    url_content = {
        f"{prefix}2026-04-10_05_00PM.pdf": content_10,
        f"{prefix}2026-04-11_05_00PM.pdf": content_11,
        f"{prefix}2026-04-12_05_00PM.pdf": content_12,
    }
    fail_urls = {f"{prefix}2026-04-12_05_00PM.pdf"}

    def mock_transport(request):
        url = str(request.url)
        if url in fail_urls:
            return httpx.Response(500, request=request)
        if url in url_content:
            return httpx.Response(
                200,
                headers={"Content-Type": "application/pdf"},
                content=url_content[url],
                request=request,
            )
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(mock_transport)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with (
        Session(engine) as session,
        NBAReportClient(
            user_agent="fixture-agent", request_interval_seconds=0, transport=transport
        ) as client,
    ):
        backfill = NBAReportBackfill(session, client)

        def process_day(current_date, probe_client):
            candidates = generate_candidate_urls(current_date)
            valid_urls = probe_candidate_urls(candidates, probe_client)
            day_discovered: list[DiscoveredReport] = []
            day_reports: list[Report] = []
            for url in valid_urls:
                report_date, report_time = parse_report_url(url)
                disc = DiscoveredReport(url, report_date, report_time, NBA_PDF_PREFIX)
                downloaded = client.download(url)
                timestamp = None
                with pdfplumber.open(BytesIO(downloaded.content)) as pdf:
                    raw_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                    match = _REPORT_TIMESTAMP_RE.search(raw_text)
                    if match:
                        timestamp = datetime.strptime(
                            " ".join(match.groups()), "%m/%d/%y %I:%M %p"
                        )
                if timestamp is None:
                    timestamp = datetime.combine(
                        disc.report_date, disc.report_time or datetime.min.time()
                    )
                day_discovered.append(disc)
                day_reports.append(Report(content=downloaded.content, timestamp=timestamp))
            selected = select_latest_reports(day_reports)
            selected_contents = {id(r.content) for r in selected}
            selected_disc = [
                disc
                for disc, report in zip(day_discovered, day_reports)
                if id(report.content) in selected_contents
            ]
            return selected_disc

        # Day 1 succeeds
        with httpx.Client(transport=transport, follow_redirects=True) as probe_client:
            selected_10 = process_day(date(2026, 4, 10), probe_client)
        reg1 = backfill.register(selected_10)
        result1 = backfill.run(date(2026, 4, 10), date(2026, 4, 10))
        assert reg1.candidates_inserted == 1
        assert result1.parsed == 1
        assert result1.entries_inserted == 1

        # Day 2 succeeds
        with httpx.Client(transport=transport, follow_redirects=True) as probe_client:
            selected_11 = process_day(date(2026, 4, 11), probe_client)
        reg2 = backfill.register(selected_11)
        result2 = backfill.run(date(2026, 4, 11), date(2026, 4, 11))
        assert reg2.candidates_inserted == 1
        assert result2.parsed == 1
        assert result2.entries_inserted == 1

        # Days 1 and 2 are persisted
        assert session.scalar(select(func.count()).select_from(NBAReportCandidate)) == 2
        assert session.scalar(select(func.count()).select_from(NBAReport)) == 2
        assert session.scalar(select(func.count()).select_from(NBAReportEntry)) == 2

        # Day 3 download fails (500) -- no candidates registered, days 1+2 survive
        with httpx.Client(transport=transport, follow_redirects=True) as probe_client:
            selected_12 = process_day(date(2026, 4, 12), probe_client)
        assert selected_12 == []
        reg3 = backfill.register(selected_12)
        assert reg3.candidates_inserted == 0
        result3 = backfill.run(date(2026, 4, 12), date(2026, 4, 12))
        assert result3.parsed == 0

        # Days 1 and 2 still intact after day 3 failure
        assert session.scalar(select(func.count()).select_from(NBAReportCandidate)) == 2
        assert session.scalar(select(func.count()).select_from(NBAReport)) == 2
        assert session.scalar(select(func.count()).select_from(NBAReportEntry)) == 2

        # Idempotency: rerunning days 1+2 creates no new records
        rerun1 = backfill.run(date(2026, 4, 10), date(2026, 4, 10))
        rerun2 = backfill.run(date(2026, 4, 11), date(2026, 4, 11))
        assert rerun1.downloaded == 0 and rerun1.parsed == 0
        assert rerun2.downloaded == 0 and rerun2.parsed == 0
        assert session.scalar(select(func.count()).select_from(NBAReportCandidate)) == 2
        assert session.scalar(select(func.count()).select_from(NBAReportEntry)) == 2


def test_direct_nba_cross_date_superseding(nba_pdf_builder):
    """A June 4 report with a June 5 game is superseded by the June 5 report for that game,
    while the June 4 report's own June 4 game entries are preserved."""
    from app.jobs.backfill_nba_reports import _suppress_superseded_game_entries
    from app.nba.discovery import NBA_PDF_PREFIX
    from app.nba.parser import Report, extract_date_matchups

    # June 4 report: covers Game A (June 4, BOS@NYK) and Game B (June 5, LAL@GSW)
    june4_page = [
        [285, 550, "Injury Report: 06/04/26 05:00 PM"],
        [24, 510, "Game Date"], [120, 510, "Game Time"], [200, 510, "Matchup"],
        [265, 510, "Team"], [426, 510, "Player Name"], [587, 510, "Current Status"],
        [667, 510, "Reason"],
        [24, 485, "06/04/2026"], [120, 485, "07:00 (ET)"], [200, 485, "BOS@NYK"],
        [265, 485, "Boston Celtics"], [426, 485, "Tatum, Jayson"],
        [587, 485, "Out"], [667, 485, "Injury/Illness - Left Ankle; Sprain"],
        [24, 460, "06/05/2026"], [120, 460, "10:00 (ET)"], [200, 460, "LAL@GSW"],
        [265, 460, "Los Angeles Lakers"], [426, 460, "Davis, Anthony"],
        [587, 460, "Questionable"], [667, 460, "Injury/Illness - Right Knee; Soreness"],
    ]

    # June 5 report: covers only Game B (June 5, LAL@GSW) with later timestamp
    june5_page = [
        [285, 550, "Injury Report: 06/05/26 05:00 PM"],
        [24, 510, "Game Date"], [120, 510, "Game Time"], [200, 510, "Matchup"],
        [265, 510, "Team"], [426, 510, "Player Name"], [587, 510, "Current Status"],
        [667, 510, "Reason"],
        [24, 485, "06/05/2026"], [120, 485, "10:00 (ET)"], [200, 485, "LAL@GSW"],
        [265, 485, "Los Angeles Lakers"], [426, 485, "Davis, Anthony"],
        [587, 485, "Out"], [667, 485, "Injury/Illness - Right Knee; Soreness"],
    ]

    june4_content = nba_pdf_builder([june4_page])
    june5_content = nba_pdf_builder([june5_page])

    url_content = {
        "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-06-04_05_00PM.pdf": june4_content,
        "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-06-05_05_00PM.pdf": june5_content,
    }

    def mock_transport(request):
        url = str(request.url)
        content = url_content.get(url)
        if content is not None:
            return httpx.Response(
                200,
                headers={"Content-Type": "application/pdf"},
                content=content,
                request=request,
            )
        return httpx.Response(404, request=request)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with (
        Session(engine) as session,
        NBAReportClient(
            user_agent="fixture-agent",
            request_interval_seconds=0,
            transport=httpx.MockTransport(mock_transport),
        ) as client,
    ):
        backfill = NBAReportBackfill(session, client)

        # --- Day 1: June 4 ---
        disc_june4 = DiscoveredReport(
            source_url="https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-06-04_05_00PM.pdf",
            report_date=date(2026, 6, 4),
            report_time=time(17),
            discovery_source_url=NBA_PDF_PREFIX,
        )
        reg1 = backfill.register([disc_june4])
        assert reg1.candidates_inserted == 1

        result1 = backfill.run(date(2026, 6, 4), date(2026, 6, 4))
        assert result1.parsed == 1
        assert result1.entries_inserted == 2

        # Two entries: one for June 4 game, one for June 5 game
        entries_day1 = list(
            session.execute(
                select(NBAReportEntry.game_date, NBAReportEntry.matchup).order_by(
                    NBAReportEntry.game_date
                )
            )
        )
        assert entries_day1 == [
            (date(2026, 6, 4), "BOS@NYK"),
            (date(2026, 6, 5), "LAL@GSW"),
        ]

        # --- Day 2: suppress before persisting ---
        june5_report = Report(content=june5_content, timestamp=datetime(2026, 6, 5, 17, 0))
        june5_game_pairs = extract_date_matchups(june5_content)
        suppressed = _suppress_superseded_game_entries(
            session, [(june5_report, june5_game_pairs)]
        )
        session.commit()

        # The June 4 report's June 5 game entry should be suppressed
        assert suppressed == 1

        # Verify: only the June 4 game entry remains
        remaining = list(
            session.execute(
                select(NBAReportEntry.game_date, NBAReportEntry.matchup)
            )
        )
        assert remaining == [(date(2026, 6, 4), "BOS@NYK")]

        # --- Day 2: register and persist June 5 report ---
        disc_june5 = DiscoveredReport(
            source_url="https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-06-05_05_00PM.pdf",
            report_date=date(2026, 6, 5),
            report_time=time(17),
            discovery_source_url=NBA_PDF_PREFIX,
        )
        reg2 = backfill.register([disc_june5])
        assert reg2.candidates_inserted == 1

        result2 = backfill.run(date(2026, 6, 5), date(2026, 6, 5))
        assert result2.parsed == 1
        assert result2.entries_inserted == 1

        # Final state: 2 reports, 2 entries total
        assert session.scalar(select(func.count()).select_from(NBAReport)) == 2
        assert session.scalar(select(func.count()).select_from(NBAReportEntry)) == 2

        final_entries = list(
            session.execute(
                select(
                    NBAReportEntry.report_id,
                    NBAReportEntry.game_date,
                    NBAReportEntry.matchup,
                ).order_by(NBAReportEntry.game_date)
            )
        )
        report_ids = {row[0] for row in final_entries}
        assert len(report_ids) == 2
        assert (date(2026, 6, 4), "BOS@NYK") in [(r[1], r[2]) for r in final_entries]
        assert (date(2026, 6, 5), "LAL@GSW") in [(r[1], r[2]) for r in final_entries]


def test_superseded_entry_survives_when_newer_report_fails_to_persist(nba_pdf_builder):
    """If the newer report's backfill.run() fails (download/parse error), the
    older snapshot's entries must remain untouched because suppression is only
    reached after a successful persist."""
    from app.nba.discovery import NBA_PDF_PREFIX

    # June 4 report: covers Game A (June 4, BOS@NYK) and Game B (June 5, LAL@GSW)
    june4_page = [
        [285, 550, "Injury Report: 06/04/26 05:00 PM"],
        [24, 510, "Game Date"], [120, 510, "Game Time"], [200, 510, "Matchup"],
        [265, 510, "Team"], [426, 510, "Player Name"], [587, 510, "Current Status"],
        [667, 510, "Reason"],
        [24, 485, "06/04/2026"], [120, 485, "07:00 (ET)"], [200, 485, "BOS@NYK"],
        [265, 485, "Boston Celtics"], [426, 485, "Tatum, Jayson"],
        [587, 485, "Out"], [667, 485, "Injury/Illness - Left Ankle; Sprain"],
        [24, 460, "06/05/2026"], [120, 460, "10:00 (ET)"], [200, 460, "LAL@GSW"],
        [265, 460, "Los Angeles Lakers"], [426, 460, "Davis, Anthony"],
        [587, 460, "Questionable"], [667, 460, "Injury/Illness - Right Knee; Soreness"],
    ]

    # June 5 report: would supersede Game B, but download will 404
    june5_page = [
        [285, 550, "Injury Report: 06/05/26 05:00 PM"],
        [24, 510, "Game Date"], [120, 510, "Game Time"], [200, 510, "Matchup"],
        [265, 510, "Team"], [426, 510, "Player Name"], [587, 510, "Current Status"],
        [667, 510, "Reason"],
        [24, 485, "06/05/2026"], [120, 485, "10:00 (ET)"], [200, 485, "LAL@GSW"],
        [265, 485, "Los Angeles Lakers"], [426, 485, "Davis, Anthony"],
        [587, 485, "Out"], [667, 485, "Injury/Illness - Right Knee; Soreness"],
    ]

    june4_content = nba_pdf_builder([june4_page])
    june5_content = nba_pdf_builder([june5_page])

    def mock_transport(request):
        url = str(request.url)
        if "2026-06-04" in url:
            return httpx.Response(
                200,
                headers={"Content-Type": "application/pdf"},
                content=june4_content,
                request=request,
            )
        # June 5 URL returns 404 — download fails, nothing persisted
        return httpx.Response(404, request=request)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with (
        Session(engine) as session,
        NBAReportClient(
            user_agent="fixture-agent",
            request_interval_seconds=0,
            transport=httpx.MockTransport(mock_transport),
        ) as client,
    ):
        backfill = NBAReportBackfill(session, client)

        # --- Day 1: persist June 4 report successfully ---
        disc_june4 = DiscoveredReport(
            source_url="https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-06-04_05_00PM.pdf",
            report_date=date(2026, 6, 4),
            report_time=time(17),
            discovery_source_url=NBA_PDF_PREFIX,
        )
        backfill.register([disc_june4])
        result1 = backfill.run(date(2026, 6, 4), date(2026, 6, 4))
        assert result1.parsed == 1
        assert result1.entries_inserted == 2

        # Old state: 2 entries for the two games
        entries_day1 = list(
            session.execute(
                select(NBAReportEntry.game_date, NBAReportEntry.matchup).order_by(
                    NBAReportEntry.game_date
                )
            )
        )
        assert entries_day1 == [
            (date(2026, 6, 4), "BOS@NYK"),
            (date(2026, 6, 5), "LAL@GSW"),
        ]

        # --- Day 2: register June 5, then backfill.run() fails (404) ---
        disc_june5 = DiscoveredReport(
            source_url="https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-06-05_05_00PM.pdf",
            report_date=date(2026, 6, 5),
            report_time=time(17),
            discovery_source_url=NBA_PDF_PREFIX,
        )
        backfill.register([disc_june5])
        result2 = backfill.run(date(2026, 6, 5), date(2026, 6, 5))
        assert result2.parsed == 0  # nothing persisted

        # With the fix, suppression only runs AFTER a successful backfill.run().
        # Since backfill.run() failed, we skip suppression entirely — old entries
        # must survive.  (Under the old code, suppression would have run before
        # backfill.run() and deleted the June 5 game entry before the failure.)

        entries_final = list(
            session.execute(
                select(NBAReportEntry.game_date, NBAReportEntry.matchup).order_by(
                    NBAReportEntry.game_date
                )
            )
        )
        assert entries_final == [
            (date(2026, 6, 4), "BOS@NYK"),
            (date(2026, 6, 5), "LAL@GSW"),
        ]


def test_superseded_entry_with_episode_linked_condition(nba_pdf_builder):
    """Suppression of a superseded entry whose condition is linked to an episode
    must delete the junction rows first, then the condition, then the entry —
    without deleting the episode itself."""
    from app.jobs.backfill_nba_reports import _suppress_superseded_game_entries
    from app.nba.discovery import NBA_PDF_PREFIX
    from app.nba.parser import Report, extract_date_matchups

    june4_page = [
        [285, 550, "Injury Report: 06/04/26 05:00 PM"],
        [24, 510, "Game Date"], [120, 510, "Game Time"], [200, 510, "Matchup"],
        [265, 510, "Team"], [426, 510, "Player Name"], [587, 510, "Current Status"],
        [667, 510, "Reason"],
        [24, 485, "06/04/2026"], [120, 485, "07:00 (ET)"], [200, 485, "BOS@NYK"],
        [265, 485, "Boston Celtics"], [426, 485, "Tatum, Jayson"],
        [587, 485, "Out"], [667, 485, "Injury/Illness - Left Ankle; Sprain"],
        [24, 460, "06/05/2026"], [120, 460, "10:00 (ET)"], [200, 460, "LAL@GSW"],
        [265, 460, "Los Angeles Lakers"], [426, 460, "Davis, Anthony"],
        [587, 460, "Questionable"], [667, 460, "Injury/Illness - Right Knee; Soreness"],
    ]

    june5_page = [
        [285, 550, "Injury Report: 06/05/26 05:00 PM"],
        [24, 510, "Game Date"], [120, 510, "Game Time"], [200, 510, "Matchup"],
        [265, 510, "Team"], [426, 510, "Player Name"], [587, 510, "Current Status"],
        [667, 510, "Reason"],
        [24, 485, "06/05/2026"], [120, 485, "10:00 (ET)"], [200, 485, "LAL@GSW"],
        [265, 485, "Los Angeles Lakers"], [426, 485, "Davis, Anthony"],
        [587, 485, "Out"], [667, 485, "Injury/Illness - Right Knee; Soreness"],
    ]

    june4_content = nba_pdf_builder([june4_page])
    june5_content = nba_pdf_builder([june5_page])

    url_content = {
        "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-06-04_05_00PM.pdf": june4_content,
        "https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-06-05_05_00PM.pdf": june5_content,
    }

    def mock_transport(request):
        url = str(request.url)
        content = url_content.get(url)
        if content is not None:
            return httpx.Response(
                200,
                headers={"Content-Type": "application/pdf"},
                content=content,
                request=request,
            )
        return httpx.Response(404, request=request)

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with (
        Session(engine) as session,
        NBAReportClient(
            user_agent="fixture-agent",
            request_interval_seconds=0,
            transport=httpx.MockTransport(mock_transport),
        ) as client,
    ):
        backfill = NBAReportBackfill(session, client)

        # --- Day 1: June 4 ---
        disc_june4 = DiscoveredReport(
            source_url="https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-06-04_05_00PM.pdf",
            report_date=date(2026, 6, 4),
            report_time=time(17),
            discovery_source_url=NBA_PDF_PREFIX,
        )
        backfill.register([disc_june4])
        result1 = backfill.run(date(2026, 6, 4), date(2026, 6, 4))
        assert result1.parsed == 1
        assert result1.entries_inserted == 2

        # Create an episode and link it to the June 5 game's condition
        davis_entry = session.execute(
            select(NBAReportEntry.id).where(
                NBAReportEntry.game_date == date(2026, 6, 5),
                NBAReportEntry.matchup == "LAL@GSW",
            )
        ).scalar_one()
        condition = session.execute(
            select(NBAInjuryCondition).where(
                NBAInjuryCondition.report_entry_id == davis_entry
            )
        ).scalar_one()

        player = NBAPlayer(canonical_name="Davis, Anthony", name_key="davis anthony")
        session.add(player)
        session.flush()

        episode = NBAInjuryEpisode(
            player_id=player.id,
            start_date=date(2026, 6, 5),
            last_observed_date=date(2026, 6, 5),
            normalized_reason="Injury/Illness - Right Knee; Soreness",
            methodology_version="v1",
        )
        session.add(episode)
        session.flush()

        junction = NBAInjuryEpisodeCondition(
            injury_episode_id=episode.id,
            injury_condition_id=condition.id,
        )
        session.add(junction)
        session.flush()

        # --- Day 2: suppress ---
        june5_report = Report(content=june5_content, timestamp=datetime(2026, 6, 5, 17, 0))
        june5_game_pairs = extract_date_matchups(june5_content)
        suppressed = _suppress_superseded_game_entries(
            session, [(june5_report, june5_game_pairs)]
        )
        session.flush()

        assert suppressed == 1

        # Junction row for the superseded condition must be gone
        remaining_junctions = session.execute(
            select(NBAInjuryEpisodeCondition.injury_condition_id)
        ).scalars().all()
        assert remaining_junctions == []

        # The superseded entry's condition must be gone; the BOS@NYK condition survives
        remaining_conditions = session.execute(
            select(NBAInjuryCondition.report_entry_id)
        ).scalars().all()
        surviving_entry = session.execute(
            select(NBAReportEntry.id).where(
                NBAReportEntry.game_date == date(2026, 6, 4)
            )
        ).scalar_one()
        assert remaining_conditions == [surviving_entry]

        # Entry row for the superseded game must be gone
        remaining_entries = session.execute(
            select(NBAReportEntry.game_date, NBAReportEntry.matchup)
        ).all()
        assert remaining_entries == [(date(2026, 6, 4), "BOS@NYK")]

        # Episode itself must survive
        assert session.get(NBAInjuryEpisode, episode.id) is not None
