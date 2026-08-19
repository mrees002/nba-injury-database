"""Add NBA schedule games table for DB-backed schedule layer.

Revision ID: 0006_nba_schedule_games
Revises: 0005_all_available_entry_type
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_nba_schedule_games"
down_revision: str | None = "0005_all_available_entry_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nba_schedule_games",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("season_type", sa.String(32), nullable=False),
        sa.Column("away_team", sa.Text(), nullable=False),
        sa.Column("home_team", sa.Text(), nullable=False),
        sa.Column("matchup", sa.String(16), nullable=False),
        sa.Column("source", sa.Text()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "season",
            "game_date",
            "matchup",
            name="uq_nba_schedule_season_date_matchup",
        ),
    )
    op.create_index("ix_nba_schedule_season", "nba_schedule_games", ["season"])
    op.create_index("ix_nba_schedule_game_date", "nba_schedule_games", ["game_date"])


def downgrade() -> None:
    op.drop_index("ix_nba_schedule_game_date", table_name="nba_schedule_games")
    op.drop_index("ix_nba_schedule_season", table_name="nba_schedule_games")
    op.drop_table("nba_schedule_games")
