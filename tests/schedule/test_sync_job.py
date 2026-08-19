from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.jobs.sync_nba_schedule import upsert_schedule_rows
from app.models.nba import NBAScheduleGame

SAMPLE_ROWS = [
    {
        "season": "2025-26",
        "game_date": "2025-10-22",
        "season_type": "regular",
        "away_team": "Boston Celtics",
        "home_team": "New York Knicks",
        "matchup": "BOS@NYK",
    },
    {
        "season": "2025-26",
        "game_date": "2025-10-22",
        "season_type": "regular",
        "away_team": "Los Angeles Lakers",
        "home_team": "Golden State Warriors",
        "matchup": "LAL@GSW",
    },
]


@pytest.fixture
def engine(tmp_path):
    database_engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(database_engine)
    try:
        yield database_engine
    finally:
        database_engine.dispose()


def test_upsert_inserts_new_rows(engine):
    with Session(engine, expire_on_commit=False) as session:
        result = upsert_schedule_rows(session, SAMPLE_ROWS, source="test")
        count = session.scalar(select(func.count()).select_from(NBAScheduleGame))

    assert result.upserted == 2
    assert result.skipped == 0
    assert count == 2


def test_upsert_is_idempotent(engine):
    with Session(engine, expire_on_commit=False) as session:
        first = upsert_schedule_rows(session, SAMPLE_ROWS)
        second = upsert_schedule_rows(session, SAMPLE_ROWS)
        count = session.scalar(select(func.count()).select_from(NBAScheduleGame))

    assert first.upserted == 2
    assert second.upserted == 0
    assert second.skipped == 2
    assert count == 2


def test_upsert_updates_changed_fields(engine):
    with Session(engine, expire_on_commit=False) as session:
        upsert_schedule_rows(session, SAMPLE_ROWS, source="v1")

        updated_row = SAMPLE_ROWS[0].copy()
        updated_row["season_type"] = "playoffs"
        updated_row["source"] = "v2"
        second = upsert_schedule_rows(session, [updated_row], source="v2")

        game = session.execute(
            select(NBAScheduleGame).where(
                NBAScheduleGame.matchup == "BOS@NYK",
                NBAScheduleGame.season == "2025-26",
            )
        ).scalar_one()

    assert second.upserted == 1
    assert second.skipped == 0
    assert game.season_type == "playoffs"
    assert game.source == "v2"
    assert game.away_team == "Boston Celtics"


def test_upsert_handles_string_dates(engine):
    rows = [
        {
            "season": "2024-25",
            "game_date": "2024-10-22",
            "season_type": "regular",
            "away_team": "BOS",
            "home_team": "NYK",
            "matchup": "BOS@NYK",
        }
    ]
    with Session(engine, expire_on_commit=False) as session:
        result = upsert_schedule_rows(session, rows)

    assert result.upserted == 1


def test_upsert_handles_date_objects(engine):
    rows = [
        {
            "season": "2024-25",
            "game_date": date(2024, 10, 22),
            "season_type": "regular",
            "away_team": "BOS",
            "home_team": "NYK",
            "matchup": "BOS@NYK",
        }
    ]
    with Session(engine, expire_on_commit=False) as session:
        result = upsert_schedule_rows(session, rows)

    assert result.upserted == 1


def test_upsert_prevents_duplicates_via_unique_constraint(engine):
    with Session(engine, expire_on_commit=False) as session:
        upsert_schedule_rows(session, SAMPLE_ROWS)

        duplicate = {
            "season": "2025-26",
            "game_date": "2025-10-22",
            "season_type": "play_in",
            "away_team": "Boston Celtics",
            "home_team": "New York Knicks",
            "matchup": "BOS@NYK",
        }
        session.add(
            NBAScheduleGame(
                season=duplicate["season"],
                game_date=date.fromisoformat(duplicate["game_date"]),
                season_type=duplicate["season_type"],
                away_team=duplicate["away_team"],
                home_team=duplicate["home_team"],
                matchup=duplicate["matchup"],
            )
        )
        with pytest.raises(Exception, match="UNIQUE"):
            session.commit()
        session.rollback()

        count = session.scalar(select(func.count()).select_from(NBAScheduleGame))
    assert count == 2
