from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

import httpx
import pdfplumber
from sqlalchemy import delete, func, select, text

from app.config import get_settings
from app.db.session import build_engine, build_session_factory
from app.models import NBAInjuryCondition, NBAInjuryEpisodeCondition, NBAReport, NBAReportEntry
from app.nba.backfill import BackfillResult, NBAReportBackfill
from app.nba.client import NBAReportClient
from app.nba.discovery import (
    CDXReportDiscovery,
    discover_from_manifest,
    generate_candidate_urls,
    parse_report_url,
    probe_candidate_urls,
)
from app.nba.parser import (
    NBAReportParseError,
    Report,
    _REPORT_TIMESTAMP_RE,
    extract_date_matchups,
    select_latest_reports_from_pairs,
)
from app.nba.types import DiscoveredReport


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _suppress_superseded_game_entries(
    session,
    new_report_game_pairs: list[tuple[Report, set[tuple[str, str]]]],
) -> int:
    """Delete entries from older reports for games now superseded by newer reports.

    For each (game_date, matchup) pair in the newly selected reports, if an
    already-persisted report covers the same game with an earlier timestamp,
    remove the older report's entries for that specific game while preserving
    its entries for other games.
    """
    if not new_report_game_pairs:
        return 0

    new_game_latest: dict[tuple[str, str], datetime] = {}
    for report, game_pairs in new_report_game_pairs:
        for pair in game_pairs:
            existing = new_game_latest.get(pair)
            if existing is None or report.timestamp > existing:
                new_game_latest[pair] = report.timestamp

    persisted = session.execute(
        select(
            NBAReportEntry.report_id,
            NBAReportEntry.game_date,
            NBAReportEntry.matchup,
            NBAReport.report_date,
            NBAReport.report_time,
        )
        .join(NBAReport, NBAReportEntry.report_id == NBAReport.id)
        .where(NBAReport.parse_status == "parsed")
    ).all()

    report_games: dict[int, tuple[datetime, set[tuple[date, str]]]] = {}
    for report_id, game_date, matchup, rpt_date, rpt_time in persisted:
        if report_id not in report_games:
            report_games[report_id] = (
                datetime.combine(rpt_date, rpt_time),
                set(),
            )
        report_games[report_id][1].add((game_date, matchup))

    deleted = 0
    for report_id, (report_time, games) in report_games.items():
        for game_date, matchup in games:
            pair_key = (game_date.strftime("%m/%d/%Y"), matchup)
            new_time = new_game_latest.get(pair_key)
            if new_time is not None and report_time < new_time:
                entry_ids = list(
                    session.scalars(
                        select(NBAReportEntry.id).where(
                            NBAReportEntry.report_id == report_id,
                            NBAReportEntry.game_date == game_date,
                            NBAReportEntry.matchup == matchup,
                        )
                    )
                )
                if entry_ids:
                    condition_ids = list(
                        session.scalars(
                            select(NBAInjuryCondition.id).where(
                                NBAInjuryCondition.report_entry_id.in_(entry_ids)
                            )
                        )
                    )
                    if condition_ids:
                        session.execute(
                            delete(NBAInjuryEpisodeCondition).where(
                                NBAInjuryEpisodeCondition.injury_condition_id.in_(
                                    condition_ids
                                )
                            )
                        )
                    session.execute(
                        delete(NBAInjuryCondition).where(
                            NBAInjuryCondition.report_entry_id.in_(entry_ids)
                        )
                    )
                    session.execute(
                        delete(NBAReportEntry).where(NBAReportEntry.id.in_(entry_ids))
                    )
                    deleted += len(entry_ids)

    if deleted:
        session.flush()
    return deleted


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(
        description="Discover, download, validate, and parse official NBA injury reports"
    )
    parser.add_argument("--start-date", type=_date, default=date(2018, 12, 20))
    parser.add_argument("--end-date", type=_date, default=date(2026, 4, 12))
    parser.add_argument("--manifest", type=Path, help="Saved newline-delimited official PDF URLs")
    parser.add_argument(
        "--registered-only",
        action="store_true",
        help="Resume candidates already stored in PostgreSQL without repeating discovery",
    )
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--direct-nba",
        action="store_true",
        help="Probe the official NBA host directly instead of querying the CDX archive index",
    )
    args = parser.parse_args()
    if args.end_date < args.start_date:
        parser.error("--end-date must not be before --start-date")
    if args.registered_only and args.manifest:
        parser.error("--registered-only cannot be combined with --manifest")
    if args.registered_only and args.discover_only:
        parser.error("--registered-only cannot be combined with --discover-only")
    if args.direct_nba and args.registered_only:
        parser.error("--direct-nba cannot be combined with --registered-only")
    if args.direct_nba and args.manifest:
        parser.error("--direct-nba cannot be combined with --manifest")

    settings = get_settings()
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
                raise RuntimeError("Another NBA report backfill process is already running")
        with (
            session_factory() as session,
            NBAReportClient(
                user_agent=settings.nba_pdf_user_agent,
                timeout_seconds=settings.nba_pdf_timeout_seconds,
                request_interval_seconds=settings.nba_pdf_request_interval_seconds,
                max_retries=settings.nba_pdf_max_retries,
                backoff_base_seconds=settings.nba_pdf_backoff_base_seconds,
            ) as client,
        ):
            backfill = NBAReportBackfill(session, client)
            if args.registered_only:
                discovered = []
            elif args.manifest:
                discovered = discover_from_manifest(
                    args.manifest.read_text(encoding="utf-8"), source=str(args.manifest.resolve())
                )
                discovered = [
                    report
                    for report in discovered
                    if args.start_date <= report.report_date <= args.end_date
                ]
            elif args.direct_nba:
                from datetime import timedelta

                from app.nba.discovery import NBA_PDF_PREFIX

                total_days = (args.end_date - args.start_date).days + 1
                combined_registration = BackfillResult()
                combined_result = BackfillResult()
                current = args.start_date
                day_index = 0
                with httpx.Client(
                    headers={"User-Agent": settings.nba_pdf_user_agent},
                    timeout=settings.nba_pdf_timeout_seconds,
                    follow_redirects=True,
                ) as probe_client:
                    while current <= args.end_date:
                        day_index += 1
                        candidates = generate_candidate_urls(current)
                        valid_urls = probe_candidate_urls(
                            candidates,
                            probe_client,
                            request_interval_seconds=settings.nba_pdf_request_interval_seconds,
                        )

                        day_discovered: list[DiscoveredReport] = []
                        day_reports_for_selection: list[Report] = []
                        for url in valid_urls:
                            report_date, report_time = parse_report_url(url)
                            disc = DiscoveredReport(
                                source_url=url,
                                report_date=report_date,
                                report_time=report_time,
                                discovery_source_url=NBA_PDF_PREFIX,
                            )
                            try:
                                downloaded = client.download(disc.source_url)
                                timestamp = None
                                with pdfplumber.open(BytesIO(downloaded.content)) as pdf:
                                    raw_text = "\n".join(
                                        page.extract_text() or "" for page in pdf.pages
                                    )
                                    match = _REPORT_TIMESTAMP_RE.search(raw_text)
                                    if match:
                                        timestamp = datetime.strptime(
                                            " ".join(match.groups()), "%m/%d/%y %I:%M %p"
                                        )
                                if timestamp is None:
                                    timestamp = datetime.combine(
                                        disc.report_date,
                                        disc.report_time or datetime.min.time(),
                                    )
                                day_discovered.append(disc)
                                day_reports_for_selection.append(
                                    Report(content=downloaded.content, timestamp=timestamp)
                                )
                            except Exception:
                                logging.warning(
                                    "Failed to download %s for selection, skipping",
                                    disc.source_url,
                                )

                        day_report_pairs: list[tuple[Report, set[tuple[str, str]]]] = []
                        for disc, report in zip(
                            day_discovered, day_reports_for_selection
                        ):
                            try:
                                game_pairs = extract_date_matchups(report.content)
                                day_report_pairs.append((report, game_pairs))
                            except NBAReportParseError as exc:
                                if "No date/matchup pairs found" in str(exc):
                                    logging.info(
                                        "Skipped non-substantive snapshot (no date/matchup pairs): "
                                        "timestamp=%s source_url=%s",
                                        report.timestamp.isoformat(),
                                        disc.source_url,
                                    )
                                else:
                                    logging.warning(
                                        "extract_date_matchups failed: timestamp=%s source_url=%s error=%s",
                                        report.timestamp.isoformat(),
                                        disc.source_url,
                                        exc,
                                    )

                        selected = select_latest_reports_from_pairs(day_report_pairs)
                        selected_contents = {id(r.content) for r in selected}
                        selected_disc = [
                            disc
                            for disc, report in zip(
                                day_discovered, day_reports_for_selection
                            )
                            if id(report.content) in selected_contents
                        ]

                        logging.info(
                            "Direct NBA day %d/%d: %s, %d discovered -> %d selected",
                            day_index,
                            total_days,
                            current.isoformat(),
                            len(day_discovered),
                            len(selected_disc),
                        )

                        day_registration = backfill.register(selected_disc)
                        combined_registration.discovered += day_registration.discovered
                        combined_registration.candidates_inserted += (
                            day_registration.candidates_inserted
                        )
                        combined_registration.already_known += day_registration.already_known

                        if not args.discover_only:
                            day_result = backfill.run(
                                current,
                                current,
                                retry_failures=args.retry_failures,
                                limit=args.limit,
                            )
                            combined_result.downloaded += day_result.downloaded
                            combined_result.missing += day_result.missing
                            combined_result.http_failures += day_result.http_failures
                            combined_result.invalid_pdfs += day_result.invalid_pdfs
                            combined_result.parsed += day_result.parsed
                            combined_result.parse_failures += day_result.parse_failures
                            combined_result.entries_inserted += day_result.entries_inserted

                        selected_pairs = [
                            (report, game_pairs)
                            for report, game_pairs in day_report_pairs
                            if id(report.content) in selected_contents
                        ]
                        suppressed = _suppress_superseded_game_entries(session, selected_pairs)
                        if suppressed:
                            session.commit()
                            logging.info(
                                "Suppressed %d entries from older reports for games "
                                "superseded by newer snapshots on %s",
                                suppressed,
                                current.isoformat(),
                            )

                        current += timedelta(days=1)

                if args.discover_only:
                    print(json.dumps(asdict(combined_registration), sort_keys=True))
                    return
                combined = asdict(combined_result)
                combined["archive_urls_discovered"] = combined_registration.discovered
                combined["candidates_inserted"] = combined_registration.candidates_inserted
                combined["candidates_already_known"] = combined_registration.already_known
                print(json.dumps(combined, sort_keys=True))
                return
            else:
                with httpx.Client(
                    headers={"User-Agent": settings.nba_pdf_user_agent},
                    timeout=settings.nba_pdf_timeout_seconds,
                    follow_redirects=True,
                ) as discovery_client:
                    discovered = CDXReportDiscovery(discovery_client).discover(
                        args.start_date, args.end_date
                    )

            registration = backfill.register(discovered)
            if args.discover_only:
                print(json.dumps(asdict(registration), sort_keys=True))
                return
            result = backfill.run(
                args.start_date,
                args.end_date,
                retry_failures=args.retry_failures,
                limit=args.limit,
            )
    finally:
        if lock_acquired:
            lock_connection.execute(
                text("select pg_advisory_unlock(:lock_key)"), {"lock_key": lock_key}
            )
        lock_connection.close()
    combined = asdict(result)
    combined["archive_urls_discovered"] = registration.discovered
    combined["candidates_inserted"] = registration.candidates_inserted
    combined["candidates_already_known"] = registration.already_known
    print(json.dumps(combined, sort_keys=True))


if __name__ == "__main__":
    main()
