from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
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
from app.nba.classification import classify_conditions
from app.nba.normalize import (
    TEAM_ABBREVIATIONS,
    canonical_player_name,
    canonical_team_name,
    player_name_key,
)
from app.nba.types import DiscoveredReport, ParsedNBAReport


def store_discoveries(session: Session, reports: list[DiscoveredReport]) -> tuple[int, int]:
    urls = [report.source_url for report in reports]
    existing = (
        set(
            session.scalars(
                select(NBAReportCandidate.source_url).where(NBAReportCandidate.source_url.in_(urls))
            )
        )
        if urls
        else set()
    )
    inserted = 0
    for report in reports:
        if report.source_url in existing:
            continue
        session.add(
            NBAReportCandidate(
                source_url=report.source_url,
                report_date=report.report_date,
                report_time=report.report_time,
                discovery_source_url=report.discovery_source_url,
                status="discovered",
                attempt_count=0,
            )
        )
        inserted += 1
    session.flush()
    return inserted, len(reports) - inserted


class EntityResolver:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.players: dict[str, NBAPlayer] = {}
        self.teams: dict[str, NBATeam] = {}
        self.games: dict[tuple[object, str], NBAGame] = {}

    def team(self, raw_name: str, abbreviation: str | None = None) -> NBATeam:
        canonical = canonical_team_name(raw_name)
        if canonical in self.teams:
            return self.teams[canonical]
        model = self.session.scalar(select(NBATeam).where(NBATeam.canonical_name == canonical))
        if model is None:
            model = NBATeam(canonical_name=canonical, abbreviation=abbreviation)
            self.session.add(model)
            self.session.flush()
        elif abbreviation and model.abbreviation is None:
            model.abbreviation = abbreviation
        self.teams[canonical] = model
        return model

    def player(self, raw_name: str) -> NBAPlayer:
        canonical = canonical_player_name(raw_name)
        key = player_name_key(canonical)
        if key in self.players:
            return self.players[key]
        model = self.session.scalar(select(NBAPlayer).where(NBAPlayer.name_key == key))
        if model is None:
            model = NBAPlayer(canonical_name=canonical, name_key=key)
            self.session.add(model)
            self.session.flush()
        self.players[key] = model
        return model

    def game(self, game_date: object, game_time: object, matchup: str) -> NBAGame:
        key = (game_date, matchup)
        if key in self.games:
            return self.games[key]
        model = self.session.scalar(
            select(NBAGame).where(NBAGame.game_date == game_date, NBAGame.matchup == matchup)
        )
        if model is None:
            away, home = matchup.split("@", 1)
            away_team = (
                self.team(TEAM_ABBREVIATIONS[away], away) if away in TEAM_ABBREVIATIONS else None
            )
            home_team = (
                self.team(TEAM_ABBREVIATIONS[home], home) if home in TEAM_ABBREVIATIONS else None
            )
            model = NBAGame(
                game_date=game_date,
                game_time=game_time,
                matchup=matchup,
                away_team_id=away_team.id if away_team else None,
                home_team_id=home_team.id if home_team else None,
            )
            self.session.add(model)
            self.session.flush()
        self.games[key] = model
        return model


def persist_parsed_report(
    session: Session,
    report: NBAReport,
    parsed: ParsedNBAReport,
    *,
    resolver: EntityResolver | None = None,
) -> int:
    existing = session.scalar(
        select(NBAReportEntry.id).where(NBAReportEntry.report_id == report.id).limit(1)
    )
    if (
        report.parse_status == "parsed"
        and existing is not None
        and report.parser_version == parsed.parser_version
    ):
        return 0
    if existing is not None:
        entry_ids = select(NBAReportEntry.id).where(NBAReportEntry.report_id == report.id)
        session.execute(
            delete(NBAInjuryCondition).where(NBAInjuryCondition.report_entry_id.in_(entry_ids))
        )
        session.execute(delete(NBAReportEntry).where(NBAReportEntry.report_id == report.id))
        session.flush()

    resolver = resolver or EntityResolver(session)
    inserted = 0
    pending_conditions: list[tuple[NBAReportEntry, int, object]] = []
    for parsed_entry in parsed.entries:
        team = resolver.team(parsed_entry.team)
        game = resolver.game(parsed_entry.game_date, parsed_entry.game_time, parsed_entry.matchup)
        player = resolver.player(parsed_entry.player_name) if parsed_entry.player_name else None
        entry = NBAReportEntry(
            report_id=report.id,
            page_number=parsed_entry.page_number,
            row_number=parsed_entry.row_number,
            game_id=game.id,
            team_id=team.id,
            player_id=player.id if player else None,
            entry_type=parsed_entry.entry_type,
            game_date=parsed_entry.game_date,
            game_time=parsed_entry.game_time,
            matchup=parsed_entry.matchup,
            team_name_raw=parsed_entry.team,
            player_name_raw=parsed_entry.player_name,
            status=parsed_entry.status,
            reason_category=parsed_entry.reason_category,
            raw_reason=parsed_entry.raw_reason,
            previous_status=parsed_entry.previous_status,
            previous_reason=parsed_entry.previous_reason,
            raw_row_text=parsed_entry.raw_row_text,
        )
        session.add(entry)
        if player is not None:
            classifications = classify_conditions(
                parsed_entry.raw_reason, parsed_entry.reason_category
            )
            pending_conditions.extend(
                (entry, condition_index, classification)
                for condition_index, classification in enumerate(classifications, start=1)
            )
        inserted += 1
    session.flush()
    session.add_all(
        [
            NBAInjuryCondition(
                report_entry_id=entry.id,
                condition_index=condition_index,
                body_part=classification.body_part,
                laterality=classification.laterality,
                injury_type=classification.injury_type,
                normalized_reason=classification.normalized_reason,
                classification_version=classification.classification_version,
                is_injury=classification.is_injury,
            )
            for entry, condition_index, classification in pending_conditions
        ]
    )
    report.report_date = parsed.report_date
    report.report_time = parsed.report_time
    report.parser_version = parsed.parser_version
    report.format_version = parsed.format_version
    report.raw_text = parsed.raw_text
    report.parse_status = "parsed"
    report.parsed_at = datetime.now(UTC)
    report.parse_error = None
    session.flush()
    return inserted
