"""Lean production daily updater for public_injury_entries.

Updates public_injury_entries directly without requiring the full archival
NBA tables (nba_report_candidates, nba_reports, nba_report_entries,
nba_injury_conditions).

For a target date:
  1. Discover official NBA injury report PDFs by probing the NBA host.
  2. Download PDFs in memory.
  3. Parse with the existing parser.
  4. Select the latest substantive reports using existing canonical selection logic.
  5. Classify primary injury condition using the existing classifier.
  6. Resolve player/team IDs using nba_players and nba_teams.
  7. Write directly to PublicInjuryEntry (superseding older reports per game).

Schedule metadata (season, season_type) is looked up from existing
nba_schedule_games rows.  The caller is responsible for keeping that
table current via the separate schedule sync tooling.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from typing import NamedTuple

import httpx
import pdfplumber
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import build_engine, build_session_factory
from app.models.nba import (
    NBAPlayer,
    NBAScheduleGame,
    NBATeam,
    PublicInjuryEntry,
)
from app.models.update_run import UpdateRun
from app.nba.classification import classify_conditions
from app.nba.client import NBAReportClient
from app.nba.discovery import (
    generate_candidate_urls,
    parse_report_url,
    probe_candidate_urls,
)
from app.nba.normalize import (
    TEAM_ABBREVIATIONS,
    canonical_player_name,
    canonical_team_name,
    player_name_key,
)
from app.nba.parser import (
    Report,
    extract_date_matchups,
    parse_report_pdf,
    select_latest_reports_from_pairs,
)


logger = logging.getLogger(__name__)


class _ReportDownload(NamedTuple):
    """A downloaded report paired with its discovery metadata."""

    source_url: str
    report_date: date
    report_time: object  # time | None
    content: bytes


class DailyUpdateResult(NamedTuple):
    """Outcome of processing a single day."""

    target_date: date
    reports_discovered: int
    reports_selected: int
    entries_written: int
    games_superseded: int


def _lookup_schedule_meta(
    session: Session, game_date: date, matchup: str
) -> tuple[str | None, str | None]:
    """Return (season, season_type) from nba_schedule_games, or (None, None)."""
    normalized_matchup = matchup.replace(" ", "")
    row = (
        session.query(NBAScheduleGame.season, NBAScheduleGame.season_type)
        .filter(
            NBAScheduleGame.game_date == game_date,
            func.replace(NBAScheduleGame.matchup, " ", "") == normalized_matchup,
        )
        .first()
    )
    if row is None:
        return None, None
    return row[0], row[1]


def _resolve_player(
    session: Session, cache: dict, raw_name: str
) -> tuple[int | None, str]:
    """Resolve or create a player, returning (player_id, canonical_name)."""
    canonical = canonical_player_name(raw_name)
    key = player_name_key(canonical)
    if key in cache:
        return cache[key]
    existing = session.query(NBAPlayer).filter(NBAPlayer.name_key == key).first()
    if existing is not None:
        cache[key] = (existing.id, existing.canonical_name)
        return existing.id, existing.canonical_name
    player = NBAPlayer(canonical_name=canonical, name_key=key)
    session.add(player)
    session.flush()
    cache[key] = (player.id, player.canonical_name)
    return player.id, player.canonical_name


def _resolve_team(
    session: Session, cache: dict, raw_name: str
) -> tuple[int | None, str]:
    """Resolve or create a team, returning (team_id, canonical_name)."""
    # First try to resolve via abbreviation (e.g. "LAL" -> "Los Angeles Lakers")
    full_name = TEAM_ABBREVIATIONS.get(raw_name.strip().upper(), raw_name)
    canonical = canonical_team_name(full_name)
    if canonical in cache:
        return cache[canonical]
    existing = session.query(NBATeam).filter(NBATeam.canonical_name == canonical).first()
    if existing is not None:
        cache[canonical] = (existing.id, existing.canonical_name)
        return existing.id, existing.canonical_name
    team = NBATeam(canonical_name=canonical)
    session.add(team)
    session.flush()
    cache[canonical] = (team.id, team.canonical_name)
    return team.id, team.canonical_name


def _write_report_entries(
    session: Session,
    report_source_url: str,
    report_date: date,
    report_time: object,
    parsed_report,
    player_cache: dict,
    team_cache: dict,
) -> tuple[int, int]:
    """Write parsed entries to PublicInjuryEntry with superseding semantics.

    Returns (entries_written, games_superseded).
    """
    player_entries = [e for e in parsed_report.entries if e.entry_type == "player"]
    if not player_entries:
        return 0, 0

    # Collect distinct games covered by this report
    games = {(e.game_date, e.matchup) for e in player_entries}

    # Find latest source report timestamp per game among existing public entries
    existing_rows = (
        session.query(PublicInjuryEntry)
        .filter(
            or_(
                *[
                    and_(
                        PublicInjuryEntry.game_date == gd,
                        PublicInjuryEntry.matchup == m,
                    )
                    for gd, m in games
                ]
            )
        )
        .all()
    )

    game_latest_timestamp: dict[tuple[date, str], datetime] = {}
    for row in existing_rows:
        key = (row.game_date, row.matchup)
        ts = datetime.combine(row.source_report_date, row.source_report_time)
        prev = game_latest_timestamp.get(key)
        if prev is None or ts > prev:
            game_latest_timestamp[key] = ts

    new_timestamp = datetime.combine(report_date, report_time)

    games_superseded = 0
    games_with_newer_existing: set[tuple[date, str]] = set()
    for game_date, matchup in games:
        existing_ts = game_latest_timestamp.get((game_date, matchup))
        if existing_ts is not None and existing_ts > new_timestamp:
            games_with_newer_existing.add((game_date, matchup))
            continue
        if existing_ts is not None and existing_ts < new_timestamp:
            games_superseded += 1
            session.query(PublicInjuryEntry).filter(
                PublicInjuryEntry.game_date == game_date,
                PublicInjuryEntry.matchup == matchup,
            ).delete()
        elif existing_ts is None:
            session.query(PublicInjuryEntry).filter(
                PublicInjuryEntry.game_date == game_date,
                PublicInjuryEntry.matchup == matchup,
            ).delete()

    # Insert new entries (skip entries for games where existing report is strictly newer)
    entries_written = 0
    for entry in player_entries:
        if (entry.game_date, entry.matchup) in games_with_newer_existing:
            continue

        classifications = classify_conditions(entry.raw_reason, entry.reason_category)
        primary = classifications[0]

        player_id, player_canonical = _resolve_player(session, player_cache, entry.player_name)
        team_id, team_canonical = _resolve_team(session, team_cache, entry.team)
        season, season_type = _lookup_schedule_meta(session, entry.game_date, entry.matchup)

        existing = (
            session.query(PublicInjuryEntry)
            .filter(
                PublicInjuryEntry.source_url == report_source_url,
                PublicInjuryEntry.row_number == entry.row_number,
            )
            .first()
        )

        if existing is not None:
            existing.game_date = entry.game_date
            existing.game_time = entry.game_time
            existing.matchup = entry.matchup
            existing.player_id = player_id
            existing.player_name = player_canonical
            existing.team_id = team_id
            existing.team_name = team_canonical
            existing.status = entry.status
            existing.raw_reason = entry.raw_reason
            existing.reason_category = entry.reason_category
            existing.body_part = primary.body_part
            existing.injury_type = primary.injury_type
            existing.season = season
            existing.season_type = season_type
        else:
            pub = PublicInjuryEntry(
                source_url=report_source_url,
                source_report_date=report_date,
                source_report_time=report_time,
                row_number=entry.row_number,
                game_date=entry.game_date,
                game_time=entry.game_time,
                matchup=entry.matchup,
                player_id=player_id,
                player_name=player_canonical,
                team_id=team_id,
                team_name=team_canonical,
                status=entry.status,
                raw_reason=entry.raw_reason,
                reason_category=entry.reason_category,
                body_part=primary.body_part,
                injury_type=primary.injury_type,
                season=season,
                season_type=season_type,
            )
            session.add(pub)
        entries_written += 1

    return entries_written, games_superseded


def update_day(session: Session, target_date: date) -> DailyUpdateResult:
    """Process a single target date end-to-end.

    Probes the NBA host for injury report PDFs, downloads and parses them,
    selects the latest reports per game, classifies injuries, resolves
    entities, and writes directly to public_injury_entries.
    """
    settings = get_settings()

    # Step 1: Generate candidate URLs and probe for valid ones
    candidate_urls = generate_candidate_urls(target_date)
    logger.info(
        "Probing %d candidate URLs for %s", len(candidate_urls), target_date.isoformat()
    )

    with httpx.Client(
        headers={"User-Agent": settings.nba_pdf_user_agent},
        timeout=settings.nba_pdf_timeout_seconds,
        follow_redirects=True,
    ) as probe_client:
        valid_urls = probe_candidate_urls(
            candidate_urls,
            probe_client,
            request_interval_seconds=settings.nba_pdf_request_interval_seconds,
        )

    if not valid_urls:
        logger.info("No valid PDFs found for %s", target_date.isoformat())
        return DailyUpdateResult(
            target_date=target_date,
            reports_discovered=0,
            reports_selected=0,
            entries_written=0,
            games_superseded=0,
        )

    logger.info("Found %d valid PDFs for %s", len(valid_urls), target_date.isoformat())

    # Step 2: Download each valid PDF in memory
    downloaded: list[_ReportDownload] = []
    with NBAReportClient(
        user_agent=settings.nba_pdf_user_agent,
        timeout_seconds=settings.nba_pdf_timeout_seconds,
        request_interval_seconds=settings.nba_pdf_request_interval_seconds,
        max_retries=settings.nba_pdf_max_retries,
        backoff_base_seconds=settings.nba_pdf_backoff_base_seconds,
    ) as client:
        for url in valid_urls:
            try:
                result = client.download(url)
                report_date, report_time = parse_report_url(url)
                downloaded.append(
                    _ReportDownload(
                        source_url=url,
                        report_date=report_date,
                        report_time=report_time,
                        content=result.content,
                    )
                )
            except Exception:
                logger.warning("Failed to download %s, skipping", url)

    # Step 3: Extract matchups and select latest reports per game
    report_pairs: list[tuple[Report, set[tuple[str, str]]]] = []
    url_for_content: dict[int, _ReportDownload] = {}
    for dl in downloaded:
        try:
            game_pairs = extract_date_matchups(dl.content)
            if not game_pairs:
                logger.info("Skipped non-substantive report: %s", dl.source_url)
                continue
            timestamp = datetime.combine(dl.report_date, dl.report_time or datetime.min.time())
            # Try extracting the actual timestamp from PDF text
            try:
                with pdfplumber.open(BytesIO(dl.content)) as pdf:
                    raw_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                    from app.nba.parser import _REPORT_TIMESTAMP_RE

                    match = _REPORT_TIMESTAMP_RE.search(raw_text)
                    if match:
                        timestamp = datetime.strptime(
                            " ".join(match.groups()), "%m/%d/%y %I:%M %p"
                        )
            except Exception:
                pass
            report = Report(content=dl.content, timestamp=timestamp)
            report_pairs.append((report, game_pairs))
            url_for_content[id(dl.content)] = dl
        except Exception:
            logger.warning("Failed to extract matchups from %s", dl.source_url)

    selected = select_latest_reports_from_pairs(report_pairs)
    selected_contents = {id(r.content) for r in selected}
    selected_downloads = [
        url_for_content[cid] for cid in selected_contents if cid in url_for_content
    ]

    logger.info(
        "Selected %d reports for %s", len(selected_downloads), target_date.isoformat()
    )

    # Step 4: Parse, classify, resolve entities, and write
    player_cache: dict[str, tuple[int | None, str]] = {}
    team_cache: dict[str, tuple[int | None, str]] = {}
    total_entries = 0
    total_superseded = 0

    for dl in selected_downloads:
        try:
            parsed_report = parse_report_pdf(dl.content, source_url=dl.source_url)
            entries_written, games_superseded = _write_report_entries(
                session,
                dl.source_url,
                parsed_report.report_date,
                parsed_report.report_time,
                parsed_report,
                player_cache,
                team_cache,
            )
            total_entries += entries_written
            total_superseded += games_superseded
        except Exception:
            logger.warning("Failed to parse %s, skipping", dl.source_url)

    session.flush()

    return DailyUpdateResult(
        target_date=target_date,
        reports_discovered=len(valid_urls),
        reports_selected=len(selected_downloads),
        entries_written=total_entries,
        games_superseded=total_superseded,
    )


def run_public_daily_update(start_date: date, end_date: date) -> None:
    """Programmatic entry point: update public_injury_entries for a date range.

    Creates and completes an UpdateRun record for the entire run.
    Uses existing nba_schedule_games rows for season/season_type metadata.
    Does not contact stats.nba.com.
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

        try:
            current = start_date
            total_discovered = 0
            total_selected = 0
            total_entries = 0
            total_superseded = 0

            while current <= end_date:
                result = update_day(session, current)
                total_discovered += result.reports_discovered
                total_selected += result.reports_selected
                total_entries += result.entries_written
                total_superseded += result.games_superseded
                current += timedelta(days=1)

            session.commit()

            run.status = "completed"
            run.finished_at = datetime.now(tz=UTC)
            run.rows_fetched = total_discovered
            run.rows_inserted = total_entries
            run.rows_processed = total_selected
            session.commit()

            logger.info(
                "Daily update complete: date=%s..%s reports_discovered=%d "
                "reports_selected=%d entries_written=%d status=completed",
                start_date.isoformat(),
                end_date.isoformat(),
                total_discovered,
                total_selected,
                total_entries,
            )

        except Exception as exc:
            run.status = "failed"
            run.finished_at = datetime.now(tz=UTC)
            run.error_details = str(exc)
            session.commit()
            logger.error(
                "Daily update failed: date=%s..%s status=failed error=%s",
                start_date.isoformat(),
                end_date.isoformat(),
                exc,
            )
            raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lean production daily updater for public_injury_entries"
    )
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        required=True,
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=None,
        help="End date (YYYY-MM-DD). Defaults to start-date.",
    )
    args = parser.parse_args()
    if args.end_date is None:
        args.end_date = args.start_date
    if args.end_date < args.start_date:
        parser.error("--end-date must not be before --start-date")

    run_public_daily_update(args.start_date, args.end_date)


if __name__ == "__main__":
    main()
