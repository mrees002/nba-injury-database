from __future__ import annotations

import csv
from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.nba import NBAScheduleGame
from app.services.schedule_import import ScheduleCSVValidationError, import_schedule_csv

SCHEDULE_HEADERS = ["season", "game_date", "season_type", "away_team", "home_team", "matchup"]

SAMPLE_ROWS = [
    ["2025-26", "2025-10-22", "regular", "Boston Celtics", "New York Knicks", "BOS@NYK"],
    ["2025-26", "2025-10-22", "regular", "Los Angeles Lakers", "Golden State Warriors", "LAL@GSW"],
    ["2025-26", "2025-10-23", "regular", "Denver Nuggets", "Phoenix Nuggets", "DEN@PHX"],
]


@pytest.fixture
def engine(tmp_path):
    database_engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(database_engine)
    try:
        yield database_engine
    finally:
        database_engine.dispose()


def write_csv(path, rows, headers=SCHEDULE_HEADERS):
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(headers)
        writer.writerows(rows)


def test_import_schedule_csv_basic(engine, tmp_path):
    csv_path = tmp_path / "schedule.csv"
    write_csv(csv_path, SAMPLE_ROWS)

    with Session(engine, expire_on_commit=False) as session:
        result = import_schedule_csv(session, csv_path, source="test")
        count = session.scalar(select(func.count()).select_from(NBAScheduleGame))
        games = list(
            session.scalars(select(NBAScheduleGame).order_by(NBAScheduleGame.game_date))
        )

    assert result.read == 3
    assert result.inserted == 3
    assert result.skipped == 0
    assert result.invalid == 0
    assert count == 3
    assert games[0].season == "2025-26"
    assert games[0].game_date == date(2025, 10, 22)
    assert games[0].season_type == "regular"
    assert games[0].away_team == "Boston Celtics"
    assert games[0].home_team == "New York Knicks"
    assert games[0].matchup == "BOS@NYK"
    assert games[0].source == "test"


def test_import_schedule_csv_is_idempotent(engine, tmp_path):
    csv_path = tmp_path / "schedule.csv"
    write_csv(csv_path, SAMPLE_ROWS)

    with Session(engine, expire_on_commit=False) as session:
        first = import_schedule_csv(session, csv_path)
        second = import_schedule_csv(session, csv_path)
        count = session.scalar(select(func.count()).select_from(NBAScheduleGame))

    assert first.inserted == 3
    assert second.inserted == 0
    assert second.skipped == 3
    assert count == 3


def test_import_schedule_csv_with_overlap(engine, tmp_path):
    file_a = tmp_path / "a.csv"
    file_b = tmp_path / "b.csv"
    write_csv(file_a, [SAMPLE_ROWS[0]])
    write_csv(file_b, [SAMPLE_ROWS[0], SAMPLE_ROWS[1]])

    with Session(engine, expire_on_commit=False) as session:
        result_a = import_schedule_csv(session, file_a)
        result_b = import_schedule_csv(session, file_b)
        count = session.scalar(select(func.count()).select_from(NBAScheduleGame))

    assert result_a.inserted == 1
    assert result_b.read == 2
    assert result_b.inserted == 1
    assert result_b.skipped == 1
    assert count == 2


def test_import_schedule_csv_rejects_missing_columns(engine, tmp_path):
    csv_path = tmp_path / "bad.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["season", "game_date", "away_team", "home_team", "matchup"])
        writer.writerow(["2025-26", "2025-10-22", "BOS", "NYK", "BOS@NYK"])

    with Session(engine, expire_on_commit=False) as session:
        with pytest.raises(ScheduleCSVValidationError, match="season_type"):
            import_schedule_csv(session, csv_path)
        count = session.scalar(select(func.count()).select_from(NBAScheduleGame))

    assert count == 0


def test_import_schedule_csv_counts_invalid_rows(engine, tmp_path):
    csv_path = tmp_path / "invalid.csv"
    write_csv(
        csv_path,
        [
            ["2025-26", "not-a-date", "regular", "BOS", "NYK", "BOS@NYK"],
            ["2025-26", "2025-10-22", "regular", "LAL", "GSW", "LAL@GSW"],
        ],
    )

    with Session(engine, expire_on_commit=False) as session:
        result = import_schedule_csv(session, csv_path)
        count = session.scalar(select(func.count()).select_from(NBAScheduleGame))

    assert result.read == 2
    assert result.inserted == 1
    assert result.invalid == 1
    assert count == 1
