from __future__ import annotations

from sqlalchemy import UniqueConstraint

from app.db.base import Base
from app.models import NBAScheduleGame


def test_nba_schedule_games_table_is_registered():
    assert "nba_schedule_games" in Base.metadata.tables


def test_nba_schedule_games_columns():
    columns = set(NBAScheduleGame.__table__.columns.keys())
    assert columns == {
        "id",
        "season",
        "game_date",
        "season_type",
        "away_team",
        "home_team",
        "matchup",
        "source",
        "updated_at",
    }


def test_nba_schedule_games_unique_constraint():
    constraints = NBAScheduleGame.__table__.constraints
    unique_constraints = [
        c for c in constraints if isinstance(c, UniqueConstraint) and c.name
    ]
    names = {c.name for c in unique_constraints}
    assert "uq_nba_schedule_season_date_matchup" in names
    constraint = next(
        c for c in unique_constraints if c.name == "uq_nba_schedule_season_date_matchup"
    )
    assert [col.name for col in constraint.columns] == [
        "season",
        "game_date",
        "matchup",
    ]


def test_nba_schedule_games_indexes():
    index_names = {idx.name for idx in NBAScheduleGame.__table__.indexes}
    assert "ix_nba_schedule_season" in index_names
    assert "ix_nba_schedule_game_date" in index_names
