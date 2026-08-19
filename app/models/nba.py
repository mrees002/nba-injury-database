from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.types import BIGINT


class NBAPlayer(Base):
    __tablename__ = "nba_players"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    name_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    official_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NBATeam(Base):
    __tablename__ = "nba_teams"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    abbreviation: Mapped[str | None] = mapped_column(String(3), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NBAGame(Base):
    __tablename__ = "nba_games"
    __table_args__ = (
        UniqueConstraint("game_date", "matchup", name="uq_nba_games_date_matchup"),
        Index("ix_nba_games_date", "game_date"),
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    game_time: Mapped[time | None] = mapped_column(Time)
    matchup: Mapped[str] = mapped_column(String(16), nullable=False)
    away_team_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("nba_teams.id"))
    home_team_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("nba_teams.id"))
    official_id: Mapped[str | None] = mapped_column(String(64), unique=True)


class NBAReportCandidate(Base):
    __tablename__ = "nba_report_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('discovered', 'downloaded', 'missing', 'http_failed', "
            "'invalid_pdf', 'parsed', 'parse_failed')",
            name="status_allowed",
        ),
        Index("ix_nba_report_candidates_date", "report_date"),
        Index("ix_nba_report_candidates_status", "status"),
        Index("ix_nba_report_candidates_resolved_report", "resolved_report_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    report_time: Mapped[time | None] = mapped_column(Time)
    discovery_source_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="discovered")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    resolved_report_id: Mapped[int | None] = mapped_column(
        BIGINT,
        ForeignKey(
            "nba_reports.id",
            name="fk_nba_report_candidates_resolved_report",
            use_alter=True,
        ),
    )
    first_discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NBAReport(Base):
    __tablename__ = "nba_reports"
    __table_args__ = (
        CheckConstraint(
            "parse_status IN ('pending', 'parsed', 'failed')", name="parse_status_allowed"
        ),
        Index("ix_nba_reports_date", "report_date"),
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("nba_report_candidates.id"), nullable=False, unique=True
    )
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    report_time: Mapped[time] = mapped_column(Time, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_length: Mapped[int] = mapped_column(Integer, nullable=False)
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    parser_version: Mapped[str | None] = mapped_column(String(32))
    format_version: Mapped[str | None] = mapped_column(String(32))
    parse_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_text: Mapped[str | None] = mapped_column(Text)
    parse_error: Mapped[str | None] = mapped_column(Text)


class NBAReportEntry(Base):
    __tablename__ = "nba_report_entries"
    __table_args__ = (
        CheckConstraint(
            "entry_type IN ('player', 'not_submitted', 'all_available')",
            name="entry_type_allowed",
        ),
        UniqueConstraint("report_id", "row_number", name="uq_nba_report_entries_report_row"),
        Index("ix_nba_report_entries_game_date", "game_date"),
        Index("ix_nba_report_entries_player", "player_id"),
        Index("ix_nba_report_entries_team", "team_id"),
        Index("ix_nba_report_entries_status", "status"),
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    report_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("nba_reports.id"), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    game_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("nba_games.id"))
    team_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("nba_teams.id"))
    player_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("nba_players.id"))
    entry_type: Mapped[str] = mapped_column(String(24), nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    game_time: Mapped[time | None] = mapped_column(Time)
    matchup: Mapped[str] = mapped_column(String(16), nullable=False)
    team_name_raw: Mapped[str] = mapped_column(Text, nullable=False)
    player_name_raw: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(64))
    reason_category: Mapped[str | None] = mapped_column(Text)
    raw_reason: Mapped[str | None] = mapped_column(Text)
    previous_status: Mapped[str | None] = mapped_column(String(64))
    previous_reason: Mapped[str | None] = mapped_column(Text)
    raw_row_text: Mapped[str] = mapped_column(Text, nullable=False)


class NBAInjuryCondition(Base):
    __tablename__ = "nba_injury_conditions"
    __table_args__ = (
        UniqueConstraint(
            "report_entry_id", "condition_index", name="uq_nba_injury_conditions_entry_index"
        ),
        Index("ix_nba_injury_conditions_body_part", "body_part"),
        Index("ix_nba_injury_conditions_injury_type", "injury_type"),
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    report_entry_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("nba_report_entries.id"), nullable=False
    )
    condition_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    body_part: Mapped[str | None] = mapped_column(Text)
    laterality: Mapped[str | None] = mapped_column(String(16))
    injury_type: Mapped[str | None] = mapped_column(Text)
    normalized_reason: Mapped[str] = mapped_column(Text, nullable=False)
    classification_version: Mapped[str] = mapped_column(String(32), nullable=False)
    is_injury: Mapped[bool] = mapped_column(Boolean, nullable=False)


class NBAInjuryEpisode(Base):
    __tablename__ = "nba_injury_episodes"
    __table_args__ = (
        Index("ix_nba_injury_episodes_player", "player_id"),
        Index("ix_nba_injury_episodes_start_date", "start_date"),
        Index("ix_nba_injury_episodes_body_part", "body_part"),
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    player_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("nba_players.id"), nullable=False)
    team_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("nba_teams.id"))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_observed_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    first_available_date: Mapped[date | None] = mapped_column(Date)
    body_part: Mapped[str | None] = mapped_column(Text)
    laterality: Mapped[str | None] = mapped_column(String(16))
    injury_type: Mapped[str | None] = mapped_column(Text)
    normalized_reason: Mapped[str] = mapped_column(Text, nullable=False)
    latest_status: Mapped[str | None] = mapped_column(String(64))
    methodology_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NBAScheduleGame(Base):
    __tablename__ = "nba_schedule_games"
    __table_args__ = (
        UniqueConstraint(
            "season", "game_date", "matchup", name="uq_nba_schedule_season_date_matchup"
        ),
        Index("ix_nba_schedule_season", "season"),
        Index("ix_nba_schedule_game_date", "game_date"),
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    season: Mapped[str] = mapped_column(Text, nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    season_type: Mapped[str] = mapped_column(String(32), nullable=False)
    away_team: Mapped[str] = mapped_column(Text, nullable=False)
    home_team: Mapped[str] = mapped_column(Text, nullable=False)
    matchup: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PublicInjuryEntry(Base):
    __tablename__ = "public_injury_entries"
    __table_args__ = (
        UniqueConstraint("source_url", "row_number", name="uq_public_injury_entries_url_row"),
        Index("ix_public_injury_entries_game_date", "game_date"),
        Index("ix_public_injury_entries_player_id", "player_id"),
        Index("ix_public_injury_entries_team_id", "team_id"),
        Index("ix_public_injury_entries_season", "season"),
        Index("ix_public_injury_entries_season_type", "season_type"),
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_report_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_report_time: Mapped[time] = mapped_column(Time, nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    game_time: Mapped[time | None] = mapped_column(Time)
    matchup: Mapped[str] = mapped_column(String(16), nullable=False)
    player_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("nba_players.id"))
    player_name: Mapped[str] = mapped_column(Text, nullable=False)
    team_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("nba_teams.id"))
    team_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str | None] = mapped_column(String(64))
    raw_reason: Mapped[str | None] = mapped_column(Text)
    reason_category: Mapped[str | None] = mapped_column(Text)
    body_part: Mapped[str | None] = mapped_column(Text)
    injury_type: Mapped[str | None] = mapped_column(Text)
    season: Mapped[str | None] = mapped_column(Text)
    season_type: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class NBAInjuryEpisodeCondition(Base):
    __tablename__ = "nba_injury_episode_conditions"
    __table_args__ = (
        UniqueConstraint(
            "injury_episode_id",
            "injury_condition_id",
            name="uq_nba_episode_conditions_episode_condition",
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    injury_episode_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(
            "nba_injury_episodes.id", name="fk_nba_episode_conditions_episode", ondelete="CASCADE"
        ),
        nullable=False,
    )
    injury_condition_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey("nba_injury_conditions.id", name="fk_nba_episode_conditions_condition"),
        nullable=False,
    )
