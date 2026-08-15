"""Add official NBA report, observation, classification, and episode tables.

Revision ID: 0002_nba_official_reports
Revises: 0001_initial_schema
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_nba_official_reports"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "nba_players",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.Column("official_id", sa.String(length=64), nullable=True),
        _timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_nba_players"),
        sa.UniqueConstraint("name_key", name="uq_nba_players_name_key"),
        sa.UniqueConstraint("official_id", name="uq_nba_players_official_id"),
    )
    op.create_table(
        "nba_teams",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("abbreviation", sa.String(length=3), nullable=True),
        _timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_nba_teams"),
        sa.UniqueConstraint("canonical_name", name="uq_nba_teams_canonical_name"),
        sa.UniqueConstraint("abbreviation", name="uq_nba_teams_abbreviation"),
    )
    op.create_table(
        "nba_report_candidates",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("report_time", sa.Time(), nullable=True),
        sa.Column("discovery_source_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "first_discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('discovered', 'downloaded', 'missing', 'http_failed', "
            "'invalid_pdf', 'parsed', 'parse_failed')",
            name=op.f("ck_nba_report_candidates_status_allowed"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nba_report_candidates"),
        sa.UniqueConstraint("source_url", name="uq_nba_report_candidates_source_url"),
    )
    op.create_index(
        "ix_nba_report_candidates_date", "nba_report_candidates", ["report_date"], unique=False
    )
    op.create_index(
        "ix_nba_report_candidates_status", "nba_report_candidates", ["status"], unique=False
    )
    op.create_table(
        "nba_games",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("game_time", sa.Time(), nullable=True),
        sa.Column("matchup", sa.String(length=16), nullable=False),
        sa.Column("away_team_id", sa.BigInteger(), nullable=True),
        sa.Column("home_team_id", sa.BigInteger(), nullable=True),
        sa.Column("official_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["away_team_id"], ["nba_teams.id"], name="fk_nba_games_away_team_id_nba_teams"
        ),
        sa.ForeignKeyConstraint(
            ["home_team_id"], ["nba_teams.id"], name="fk_nba_games_home_team_id_nba_teams"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nba_games"),
        sa.UniqueConstraint("official_id", name="uq_nba_games_official_id"),
        sa.UniqueConstraint("game_date", "matchup", name="uq_nba_games_date_matchup"),
    )
    op.create_index("ix_nba_games_date", "nba_games", ["game_date"], unique=False)
    op.create_table(
        "nba_reports",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("report_time", sa.Time(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("byte_length", sa.Integer(), nullable=False),
        sa.Column(
            "downloaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("parser_version", sa.String(length=32), nullable=True),
        sa.Column("format_version", sa.String(length=32), nullable=True),
        sa.Column("parse_status", sa.String(length=16), nullable=False),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "parse_status IN ('pending', 'parsed', 'failed')",
            name=op.f("ck_nba_reports_parse_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["nba_report_candidates.id"],
            name="fk_nba_reports_candidate_id_nba_report_candidates",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nba_reports"),
        sa.UniqueConstraint("candidate_id", name="uq_nba_reports_candidate_id"),
        sa.UniqueConstraint("content_hash", name="uq_nba_reports_content_hash"),
        sa.UniqueConstraint("source_url", name="uq_nba_reports_source_url"),
    )
    op.create_index("ix_nba_reports_date", "nba_reports", ["report_date"], unique=False)
    op.create_table(
        "nba_report_entries",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.BigInteger(), nullable=True),
        sa.Column("team_id", sa.BigInteger(), nullable=True),
        sa.Column("player_id", sa.BigInteger(), nullable=True),
        sa.Column("entry_type", sa.String(length=24), nullable=False),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("game_time", sa.Time(), nullable=True),
        sa.Column("matchup", sa.String(length=16), nullable=False),
        sa.Column("team_name_raw", sa.Text(), nullable=False),
        sa.Column("player_name_raw", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("reason_category", sa.Text(), nullable=True),
        sa.Column("raw_reason", sa.Text(), nullable=True),
        sa.Column("raw_row_text", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "entry_type IN ('player', 'not_submitted')",
            name=op.f("ck_nba_report_entries_entry_type_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["game_id"], ["nba_games.id"], name="fk_nba_report_entries_game_id_nba_games"
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["nba_players.id"],
            name="fk_nba_report_entries_player_id_nba_players",
        ),
        sa.ForeignKeyConstraint(
            ["report_id"], ["nba_reports.id"], name="fk_nba_report_entries_report_id_nba_reports"
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["nba_teams.id"], name="fk_nba_report_entries_team_id_nba_teams"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nba_report_entries"),
        sa.UniqueConstraint("report_id", "row_number", name="uq_nba_report_entries_report_row"),
    )
    for name, columns in (
        ("ix_nba_report_entries_game_date", ["game_date"]),
        ("ix_nba_report_entries_player", ["player_id"]),
        ("ix_nba_report_entries_team", ["team_id"]),
        ("ix_nba_report_entries_status", ["status"]),
    ):
        op.create_index(name, "nba_report_entries", columns, unique=False)
    op.create_table(
        "nba_injury_conditions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("report_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("condition_index", sa.Integer(), nullable=False),
        sa.Column("body_part", sa.Text(), nullable=True),
        sa.Column("laterality", sa.String(length=16), nullable=True),
        sa.Column("injury_type", sa.Text(), nullable=True),
        sa.Column("normalized_reason", sa.Text(), nullable=False),
        sa.Column("classification_version", sa.String(length=32), nullable=False),
        sa.Column("is_injury", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["report_entry_id"],
            ["nba_report_entries.id"],
            name="fk_nba_injury_conditions_report_entry_id_nba_report_entries",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nba_injury_conditions"),
        sa.UniqueConstraint(
            "report_entry_id",
            "condition_index",
            name="uq_nba_injury_conditions_entry_index",
        ),
    )
    op.create_index(
        "ix_nba_injury_conditions_body_part",
        "nba_injury_conditions",
        ["body_part"],
        unique=False,
    )
    op.create_index(
        "ix_nba_injury_conditions_injury_type",
        "nba_injury_conditions",
        ["injury_type"],
        unique=False,
    )
    op.create_table(
        "nba_injury_episodes",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("last_observed_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("first_available_date", sa.Date(), nullable=True),
        sa.Column("body_part", sa.Text(), nullable=True),
        sa.Column("laterality", sa.String(length=16), nullable=True),
        sa.Column("injury_type", sa.Text(), nullable=True),
        sa.Column("normalized_reason", sa.Text(), nullable=False),
        sa.Column("latest_status", sa.String(length=64), nullable=True),
        sa.Column("methodology_version", sa.String(length=32), nullable=False),
        _timestamps(),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["nba_players.id"],
            name="fk_nba_injury_episodes_player_id_nba_players",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["nba_teams.id"], name="fk_nba_injury_episodes_team_id_nba_teams"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nba_injury_episodes"),
    )
    for name, columns in (
        ("ix_nba_injury_episodes_player", ["player_id"]),
        ("ix_nba_injury_episodes_start_date", ["start_date"]),
        ("ix_nba_injury_episodes_body_part", ["body_part"]),
    ):
        op.create_index(name, "nba_injury_episodes", columns, unique=False)
    op.create_table(
        "nba_injury_episode_conditions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("injury_episode_id", sa.BigInteger(), nullable=False),
        sa.Column("injury_condition_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["injury_episode_id"],
            ["nba_injury_episodes.id"],
            name="fk_nba_episode_conditions_episode",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["injury_condition_id"],
            ["nba_injury_conditions.id"],
            name="fk_nba_episode_conditions_condition",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nba_injury_episode_conditions"),
        sa.UniqueConstraint(
            "injury_episode_id",
            "injury_condition_id",
            name="uq_nba_episode_conditions_episode_condition",
        ),
    )


def downgrade() -> None:
    op.drop_table("nba_injury_episode_conditions")
    for name in (
        "ix_nba_injury_episodes_body_part",
        "ix_nba_injury_episodes_start_date",
        "ix_nba_injury_episodes_player",
    ):
        op.drop_index(name, table_name="nba_injury_episodes")
    op.drop_table("nba_injury_episodes")
    op.drop_index("ix_nba_injury_conditions_injury_type", table_name="nba_injury_conditions")
    op.drop_index("ix_nba_injury_conditions_body_part", table_name="nba_injury_conditions")
    op.drop_table("nba_injury_conditions")
    for name in (
        "ix_nba_report_entries_status",
        "ix_nba_report_entries_team",
        "ix_nba_report_entries_player",
        "ix_nba_report_entries_game_date",
    ):
        op.drop_index(name, table_name="nba_report_entries")
    op.drop_table("nba_report_entries")
    op.drop_index("ix_nba_reports_date", table_name="nba_reports")
    op.drop_table("nba_reports")
    op.drop_index("ix_nba_games_date", table_name="nba_games")
    op.drop_table("nba_games")
    op.drop_index("ix_nba_report_candidates_status", table_name="nba_report_candidates")
    op.drop_index("ix_nba_report_candidates_date", table_name="nba_report_candidates")
    op.drop_table("nba_report_candidates")
    op.drop_table("nba_teams")
    op.drop_table("nba_players")
