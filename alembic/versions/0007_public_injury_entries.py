"""Create public_injury_entries table for lean public production layer.

Revision ID: 0007_public_injury_entries
Revises: 0006_nba_schedule_games
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_public_injury_entries"
down_revision: str | None = "0006_nba_schedule_games"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "public_injury_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_report_date", sa.Date(), nullable=False),
        sa.Column("source_report_time", sa.Time(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("game_time", sa.Time()),
        sa.Column("matchup", sa.String(16), nullable=False),
        sa.Column("player_id", sa.BigInteger(), sa.ForeignKey("nba_players.id")),
        sa.Column("player_name", sa.Text(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), sa.ForeignKey("nba_teams.id")),
        sa.Column("team_name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(64)),
        sa.Column("raw_reason", sa.Text()),
        sa.Column("reason_category", sa.Text()),
        sa.Column("body_part", sa.Text()),
        sa.Column("injury_type", sa.Text()),
        sa.Column("season", sa.Text()),
        sa.Column("season_type", sa.String(32)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "source_url",
            "row_number",
            name="uq_public_injury_entries_url_row",
        ),
    )
    op.create_index(
        "ix_public_injury_entries_game_date", "public_injury_entries", ["game_date"]
    )
    op.create_index(
        "ix_public_injury_entries_player_id", "public_injury_entries", ["player_id"]
    )
    op.create_index(
        "ix_public_injury_entries_team_id", "public_injury_entries", ["team_id"]
    )
    op.create_index(
        "ix_public_injury_entries_season", "public_injury_entries", ["season"]
    )
    op.create_index(
        "ix_public_injury_entries_season_type", "public_injury_entries", ["season_type"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_public_injury_entries_season_type", table_name="public_injury_entries"
    )
    op.drop_index("ix_public_injury_entries_season", table_name="public_injury_entries")
    op.drop_index("ix_public_injury_entries_team_id", table_name="public_injury_entries")
    op.drop_index(
        "ix_public_injury_entries_player_id", table_name="public_injury_entries"
    )
    op.drop_index(
        "ix_public_injury_entries_game_date", table_name="public_injury_entries"
    )
    op.drop_table("public_injury_entries")
