from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from legacy.process_injuries_pipeline import (
    DEDUP_SCORES,
    TIME_WINDOWS,
    extract_injury_info,
    get_nba_season,
    is_recovery_note,
    process_dataset,
    process_injuries,
)

RAW_COLUMNS = ["Date", "Team", "Acquired", "Relinquished", "Notes"]


def raw_row(
    row_date: str,
    notes: str,
    *,
    player: str = "Test Player",
    team: str = "BOS",
) -> dict[str, object]:
    return {
        "Date": row_date,
        "Team": team,
        "Acquired": pd.NA,
        "Relinquished": player,
        "Notes": notes,
    }


def run_pipeline(
    tmp_path,
    *,
    il_rows: list[dict[str, object]],
    missed_rows: list[dict[str, object]],
) -> pd.DataFrame:
    il_path = tmp_path / "il.csv"
    missed_path = tmp_path / "missed.csv"
    pd.DataFrame(il_rows, columns=RAW_COLUMNS).to_csv(il_path, index=False)
    pd.DataFrame(missed_rows, columns=RAW_COLUMNS).to_csv(missed_path, index=False)
    return process_injuries(il_path, missed_path)


@pytest.mark.parametrize(
    ("notes", "expected"),
    [
        ("torn left ACL", ("knee", "ACL tear")),
        ("sprained right MCL", ("knee", "MCL injury")),
        ("ruptured PCL", ("knee", "PCL tear")),
        ("ruptured left Achilles tendon", ("achilles", "tear")),
        ("strained right calf", ("calf", "strain")),
        ("underwent surgery on right knee", ("knee", "surgery")),
        ("broken left wrist", ("wrist", "fracture")),
        ("concussion", (None, "concussion")),
        ("out with flu-like illness", ("illness", "illness")),
        ("fined by coach", (None, "non-injury")),
    ],
)
def test_extract_injury_info_characterization(notes, expected):
    assert extract_injury_info(notes) == expected


def test_extract_injury_info_returns_no_classification_for_missing_notes():
    assert extract_injury_info(pd.NA) == (None, None)


@pytest.mark.parametrize(
    "notes",
    [
        "recovering from surgery on left Achilles",
        "Placed on IL recovering from ACL reconstruction",
        "RECOVERING FROM SURGERY on right calf",
    ],
)
def test_is_recovery_note_matches_the_two_legacy_phrases(notes):
    assert is_recovery_note(notes) is True


@pytest.mark.parametrize(
    "notes",
    [
        pd.NA,
        "underwent surgery on left knee",
        "recovering from an injury",
    ],
)
def test_is_recovery_note_does_not_generalize_beyond_legacy_phrases(notes):
    assert is_recovery_note(notes) is False


def test_recovery_note_is_classified_before_other_injury_keywords():
    assert extract_injury_info("recovering from surgery for torn Achilles") == (
        "achilles",
        "recovery",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2024, 9, 30), "2023-24"),
        (date(2024, 10, 1), "2024-25"),
        (pd.Timestamp("2025-01-01"), "2024-25"),
    ],
)
def test_get_nba_season_uses_october_as_the_boundary(value, expected):
    assert get_nba_season(value) == expected


def test_process_dataset_skips_acquired_only_rows_and_cleans_player_prefix():
    source = pd.DataFrame(
        [
            {
                "Date": "2024-01-01",
                "Team": "BOS",
                "Acquired": "Returning Player",
                "Relinquished": pd.NA,
                "Notes": "activated",
            },
            {
                "Date": "2024-01-02",
                "Team": "LAL",
                "Acquired": pd.NA,
                "Relinquished": "  • Injured Player  ",
                "Notes": "sprained left ankle",
            },
            {
                "Date": "2024-01-03",
                "Team": "NYK",
                "Acquired": "Incoming Player",
                "Relinquished": "•Outgoing Player",
                "Notes": "sore right knee",
            },
        ]
    )

    result = process_dataset(source, "NBA_IL")

    assert result["player_name"].tolist() == ["Injured Player", "Outgoing Player"]
    assert result["source"].tolist() == ["NBA_IL", "NBA_IL"]
    assert result["body_part"].tolist() == ["ankle", "knee"]
    assert result["injury_type"].tolist() == ["sprain", "soreness"]


def test_process_dataset_preserves_unclassified_relinquished_rows():
    source = pd.DataFrame(
        [
            {
                "Date": "2024-01-01",
                "Team": "BOS",
                "Acquired": pd.NA,
                "Relinquished": "Test Player",
                "Notes": pd.NA,
            }
        ]
    )

    result = process_dataset(source, "NBA_Missed_Games")

    assert len(result) == 1
    assert pd.isna(result.loc[0, "body_part"])
    assert pd.isna(result.loc[0, "injury_type"])
    assert result.loc[0, "notes_length"] == 0


def test_exact_duplicate_scoring_prefers_il_record(tmp_path):
    il_note = "placed on IL with sore left knee"
    missed_note = "sore left knee after a game " + ("with additional detail " * 10)

    result = run_pipeline(
        tmp_path,
        il_rows=[raw_row("2024-01-01", il_note)],
        missed_rows=[raw_row("2024-01-01", missed_note)],
    )

    assert len(result) == 1
    assert result.iloc[0]["notes"] == il_note


def test_il_preference_is_a_weight_not_an_absolute_priority(tmp_path):
    il_note = "sore left knee"
    missed_note = "sore left knee " + ("x" * 1_100)

    result = run_pipeline(
        tmp_path,
        il_rows=[raw_row("2024-01-01", il_note)],
        missed_rows=[raw_row("2024-01-01", missed_note)],
    )

    assert len(result) == 1
    assert result.iloc[0]["notes"] == missed_note


def test_exact_duplicate_scoring_rewards_placed_on_il_text(tmp_path):
    placed_note = "placed on IL with sore left knee"
    longer_note = "sore left knee " + ("after evaluation " * 10)

    result = run_pipeline(
        tmp_path,
        il_rows=[
            raw_row("2024-01-01", placed_note),
            raw_row("2024-01-01", longer_note),
        ],
        missed_rows=[],
    )

    assert len(result) == 1
    assert result.iloc[0]["notes"] == placed_note


def test_documented_score_constants_do_not_control_nested_scoring(tmp_path, monkeypatch):
    monkeypatch.setitem(DEDUP_SCORES, "il_dataset", -10_000)
    monkeypatch.setitem(DEDUP_SCORES, "placed_on_il", -10_000)
    il_note = "placed on IL with sore left knee"
    missed_note = "sore left knee"

    result = run_pipeline(
        tmp_path,
        il_rows=[raw_row("2024-01-01", il_note)],
        missed_rows=[raw_row("2024-01-01", missed_note)],
    )

    assert result.iloc[0]["notes"] == il_note


def test_recovery_notes_are_removed_by_public_pipeline(tmp_path):
    result = run_pipeline(
        tmp_path,
        il_rows=[
            raw_row(
                "2024-01-01",
                "recovering from surgery on left Achilles",
                player="Recovering Player",
            ),
            raw_row(
                "2024-01-02",
                "sprained left ankle",
                player="Injured Player",
            ),
        ],
        missed_rows=[],
    )

    assert result["player_name"].tolist() == ["Injured Player"]


def test_standard_time_window_drops_day_30_and_keeps_day_31(tmp_path):
    result = run_pipeline(
        tmp_path,
        il_rows=[
            raw_row("2024-01-01", "sprained left ankle"),
            raw_row("2024-01-31", "sprained left ankle"),
            raw_row("2024-02-01", "sprained left ankle"),
        ],
        missed_rows=[],
    )

    assert result["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2024-01-01",
        "2024-02-01",
    ]


def test_documented_time_window_constants_do_not_control_nested_dedup(tmp_path, monkeypatch):
    monkeypatch.setitem(TIME_WINDOWS, "standard", 0)

    result = run_pipeline(
        tmp_path,
        il_rows=[
            raw_row("2024-01-01", "sprained left ankle"),
            raw_row("2024-01-02", "sprained left ankle"),
        ],
        missed_rows=[],
    )

    assert result["date"].dt.strftime("%Y-%m-%d").tolist() == ["2024-01-01"]


def test_acl_tear_suppresses_surgery_until_severe_base_window_expires(tmp_path):
    start = pd.Timestamp("2023-01-01")
    result = run_pipeline(
        tmp_path,
        il_rows=[
            raw_row(start.strftime("%Y-%m-%d"), "torn left ACL"),
            raw_row(
                (start + pd.Timedelta(days=180)).strftime("%Y-%m-%d"),
                "underwent surgery on left knee",
            ),
            raw_row(
                (start + pd.Timedelta(days=181)).strftime("%Y-%m-%d"),
                "underwent surgery on left knee",
            ),
            raw_row(
                (start + pd.Timedelta(days=366)).strftime("%Y-%m-%d"),
                "underwent surgery on left knee",
            ),
        ],
        missed_rows=[],
    )

    assert result["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2023-01-01",
        "2024-01-02",
    ]
    assert result["injury_type"].tolist() == ["ACL tear", "surgery"]


def test_achilles_follow_up_window_is_inclusive_through_750_days(tmp_path):
    start = pd.Timestamp("2021-01-01")
    result = run_pipeline(
        tmp_path,
        il_rows=[
            raw_row(start.strftime("%Y-%m-%d"), "torn left Achilles"),
            raw_row(
                (start + pd.Timedelta(days=750)).strftime("%Y-%m-%d"),
                "left Achilles injury",
            ),
            raw_row(
                (start + pd.Timedelta(days=751)).strftime("%Y-%m-%d"),
                "left Achilles injury",
            ),
        ],
        missed_rows=[],
    )

    assert result["date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2021-01-01",
        "2023-01-22",
    ]


def test_public_pipeline_drops_concussion_without_a_body_part(tmp_path):
    result = run_pipeline(
        tmp_path,
        il_rows=[
            raw_row("2024-01-01", "concussion", player="Concussed Player"),
            raw_row("2024-01-02", "sprained left ankle", player="Other Player"),
        ],
        missed_rows=[],
    )

    assert result["player_name"].tolist() == ["Other Player"]
