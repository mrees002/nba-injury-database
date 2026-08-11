from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import Injury, RawTransaction
from app.processing import (
    extract_injury_info,
    get_nba_season,
    is_recovery_note,
    process_raw_transactions,
    rebuild_injuries,
)
from legacy.process_injuries_pipeline import (
    extract_injury_info as legacy_extract_injury_info,
)
from legacy.process_injuries_pipeline import get_nba_season as legacy_get_nba_season
from legacy.process_injuries_pipeline import is_recovery_note as legacy_is_recovery_note
from legacy.process_injuries_pipeline import process_injuries as legacy_process_injuries

RAW_COLUMNS = ["Date", "Team", "Acquired", "Relinquished", "Notes"]


def raw_transaction(
    raw_id: int,
    source_type: str,
    row_date: str,
    notes: str,
    *,
    player: str = "Test Player",
    team: str = "BOS",
    acquired: str = "",
) -> RawTransaction:
    return RawTransaction(
        id=raw_id,
        source_type=source_type,
        transaction_date=date.fromisoformat(row_date),
        team=team,
        acquired=acquired,
        relinquished=player,
        notes=notes,
        source_url=None,
        source_row_key=f"key-{raw_id}",
    )


@pytest.mark.parametrize(
    "notes",
    [
        "torn left ACL",
        "sprained right MCL",
        "ruptured PCL",
        "ruptured left Achilles tendon",
        "strained right calf",
        "underwent surgery on right knee",
        "recovering from surgery on left Achilles",
        "broken left wrist",
        "concussion",
        "out with flu-like illness",
        "fined by coach",
        pd.NA,
    ],
)
def test_sql_classifier_matches_legacy(notes):
    assert extract_injury_info(notes) == legacy_extract_injury_info(notes)
    assert is_recovery_note(notes) == legacy_is_recovery_note(notes)


@pytest.mark.parametrize(
    "value",
    [date(2024, 9, 30), date(2024, 10, 1), pd.Timestamp("2025-01-01")],
)
def test_sql_season_assignment_matches_legacy(value):
    assert get_nba_season(value) == legacy_get_nba_season(value)


def test_sql_pipeline_matches_legacy_pipeline_on_regression_fixture(tmp_path):
    il_rows = [
        ["2023-01-01", "BOS", "", "Test Player", "torn left ACL"],
        ["2023-06-30", "BOS", "", "Test Player", "underwent surgery on left knee"],
        ["2024-01-03", "LAL", "", "Other Player", "placed on IL with sore right knee"],
        ["2024-01-04", "NYK", "Returning Player", "", "activated"],
        ["2024-01-05", "MIA", "", "Recovering Player", "recovering from surgery on ACL"],
        ["2024-01-06", "CHI", "", "Concussed Player", "concussion"],
    ]
    missed_rows = [
        [
            "2024-01-03",
            "LAL",
            "",
            "Other Player",
            "sore right knee after evaluation with additional detail",
        ],
        ["2024-01-07", "DAL", "", "Third Player", "sprained left ankle"],
    ]
    il_path = tmp_path / "il.csv"
    missed_path = tmp_path / "missed.csv"
    pd.DataFrame(il_rows, columns=RAW_COLUMNS).to_csv(il_path, index=False)
    pd.DataFrame(missed_rows, columns=RAW_COLUMNS).to_csv(missed_path, index=False)

    raw_rows = []
    raw_id = 1
    for source_type, rows in [("il", il_rows), ("missed_game", missed_rows)]:
        for row in rows:
            raw_rows.append(
                RawTransaction(
                    id=raw_id,
                    source_type=source_type,
                    transaction_date=date.fromisoformat(row[0]),
                    team=row[1],
                    acquired=row[2],
                    relinquished=row[3],
                    notes=row[4],
                    source_url=None,
                    source_row_key=f"key-{raw_id}",
                )
            )
            raw_id += 1

    legacy_result = legacy_process_injuries(il_path, missed_path)
    sql_result = process_raw_transactions(raw_rows)

    pd.testing.assert_frame_equal(
        sql_result[legacy_result.columns].reset_index(drop=True),
        legacy_result.reset_index(drop=True),
        check_dtype=False,
    )


def test_il_preference_carries_selected_raw_transaction_lineage():
    il_row = raw_transaction(
        10,
        "il",
        "2024-01-01",
        "placed on IL with sore left knee",
    )
    missed_row = raw_transaction(
        20,
        "missed_game",
        "2024-01-01",
        "sore left knee after evaluation",
    )

    result = process_raw_transactions([il_row, missed_row])

    assert len(result) == 1
    assert result.iloc[0]["preferred_source"] == "il"
    assert result.iloc[0]["source_raw_transaction_id"] == 10


def test_equal_score_same_day_records_preserve_sql_input_tie_order():
    bursitis = raw_transaction(
        37957,
        "missed_game",
        "2018-03-27",
        "bursitis in knee (DTD)",
        player="Dwight Powell",
        team="Mavericks",
    )
    generic_injury = raw_transaction(
        37958,
        "missed_game",
        "2018-03-27",
        "left knee injury (DTD)",
        player="Dwight Powell",
        team="Mavericks",
    )
    assert len(bursitis.notes) == len(generic_injury.notes)

    result = process_raw_transactions([bursitis, generic_injury])

    assert len(result) == 1
    assert result.iloc[0]["notes"] == "bursitis in knee (DTD)"
    assert result.iloc[0]["source_raw_transaction_id"] == 37957


def test_rebuild_is_repeatable_and_preserves_lineage(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'rebuild.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    try:
        session.add_all(
            [
                raw_transaction(1, "il", "2024-01-01", "sprained left ankle"),
                raw_transaction(2, "il", "2024-02-15", "sprained left ankle"),
                raw_transaction(
                    3,
                    "missed_game",
                    "2024-03-01",
                    "out with flu-like illness",
                    player="Sick Player",
                ),
            ]
        )
        session.commit()

        first_result = rebuild_injuries(session)
        first_snapshot = list(
            session.execute(
                select(
                    Injury.date,
                    Injury.season,
                    Injury.player_name,
                    Injury.team,
                    Injury.body_part,
                    Injury.injury_type,
                    Injury.notes,
                    Injury.preferred_source,
                    Injury.source_raw_transaction_id,
                ).order_by(Injury.date, Injury.player_name)
            )
        )
        second_result = rebuild_injuries(session)
        second_snapshot = list(
            session.execute(
                select(
                    Injury.date,
                    Injury.season,
                    Injury.player_name,
                    Injury.team,
                    Injury.body_part,
                    Injury.injury_type,
                    Injury.notes,
                    Injury.preferred_source,
                    Injury.source_raw_transaction_id,
                ).order_by(Injury.date, Injury.player_name)
            )
        )
    finally:
        session.close()
        engine.dispose()

    assert first_result.raw_rows == 3
    assert first_result.injury_rows == 3
    assert second_result == first_result
    assert second_snapshot == first_snapshot
    assert all(row.source_raw_transaction_id is not None for row in second_snapshot)
