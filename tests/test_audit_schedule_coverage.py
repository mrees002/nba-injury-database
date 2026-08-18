"""Offline tests for scripts.audit_schedule_coverage."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.nba import Base
from scripts.audit_schedule_coverage import (
    _load_reference_by_month,
    _load_reference_by_season_type,
    _load_reference_details,
    _pct,
    audit,
    load_reference,
    query_observed_games,
    query_observed_games_with_all_available,
)


def _write_reference(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["season", "game_date", "season_type", "away_team", "home_team", "matchup"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _sample_rows() -> list[dict[str, str]]:
    return [
        {
            "season": "2024-25",
            "game_date": "2024-10-22",
            "season_type": "regular",
            "away_team": "Boston Celtics",
            "home_team": "New York Knicks",
            "matchup": "BOS@NYK",
        },
        {
            "season": "2024-25",
            "game_date": "2024-10-22",
            "season_type": "regular",
            "away_team": "Los Angeles Lakers",
            "home_team": "Golden State Warriors",
            "matchup": "LAL@GSW",
        },
        {
            "season": "2024-25",
            "game_date": "2024-10-08",
            "season_type": "preseason",
            "away_team": "Denver Nuggets",
            "home_team": "Phoenix Suns",
            "matchup": "DEN@PHX",
        },
        {
            "season": "2024-25",
            "game_date": "2025-04-15",
            "season_type": "play_in",
            "away_team": "Miami Heat",
            "home_team": "Chicago Bulls",
            "matchup": "MIA@CHI",
        },
        {
            "season": "2024-25",
            "game_date": "2025-05-01",
            "season_type": "playoffs",
            "away_team": "Boston Celtics",
            "home_team": "Milwaukee Bucks",
            "matchup": "BOS@MIL",
        },
        {
            "season": "2025-26",
            "game_date": "2025-10-22",
            "season_type": "regular",
            "away_team": "Oklahoma City Thunder",
            "home_team": "Denver Nuggets",
            "matchup": "OKC@DEN",
        },
    ]


def _make_entry(
    game_date: date,
    matchup: str,
    entry_type: str = "player",
    report_id: int = 1,
    row_number: int = 1,
) -> dict:
    return {
        "report_id": report_id,
        "page_number": 1,
        "row_number": row_number,
        "entry_type": entry_type,
        "game_date": game_date,
        "matchup": matchup,
        "team_name_raw": "Test",
        "raw_row_text": "test",
    }


def _make_db(entries: list[dict]) -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    for i, e in enumerate(entries):
        e = {**e, "row_number": i + 1}
        session.execute(
            Base.metadata.tables["nba_report_entries"].insert().values(**e)
        )
    session.commit()
    return session


# ---------------------------------------------------------------------------
# load_reference
# ---------------------------------------------------------------------------


class TestLoadReference:
    def test_valid_reference(self, tmp_path: Path) -> None:
        path = tmp_path / "ref.csv"
        _write_reference(path, _sample_rows())
        rows = load_reference(path)
        assert len(rows) == 6

    def test_invalid_season_type_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "ref.csv"
        rows = _sample_rows()
        rows[0]["season_type"] = "all_star"
        _write_reference(path, rows)
        with pytest.raises(ValueError, match="Invalid season_type"):
            load_reference(path)


# ---------------------------------------------------------------------------
# _load_reference_by_season_type
# ---------------------------------------------------------------------------


class TestLoadReferenceBySeasonType:
    def test_groups_correctly(self, tmp_path: Path) -> None:
        path = tmp_path / "ref.csv"
        _write_reference(path, _sample_rows())
        result = _load_reference_by_season_type(path)

        assert "2024-25" in result
        assert "regular" in result["2024-25"]
        assert len(result["2024-25"]["regular"]) == 2
        assert ("2024-10-22", "BOS@NYK") in result["2024-25"]["regular"]

    def test_preseason_separate(self, tmp_path: Path) -> None:
        path = tmp_path / "ref.csv"
        _write_reference(path, _sample_rows())
        result = _load_reference_by_season_type(path)

        assert "preseason" in result["2024-25"]
        assert len(result["2024-25"]["preseason"]) == 1
        assert ("2024-10-08", "DEN@PHX") in result["2024-25"]["preseason"]

    def test_multiple_seasons(self, tmp_path: Path) -> None:
        path = tmp_path / "ref.csv"
        _write_reference(path, _sample_rows())
        result = _load_reference_by_season_type(path)

        assert "2025-26" in result
        assert len(result["2025-26"]["regular"]) == 1


# ---------------------------------------------------------------------------
# _load_reference_by_month
# ---------------------------------------------------------------------------


class TestLoadReferenceByMonth:
    def test_month_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "ref.csv"
        _write_reference(path, _sample_rows())
        result = _load_reference_by_month(path)

        assert "2024-25" in result
        months = result["2024-25"]["regular"]
        assert "2024-10" in months
        assert len(months["2024-10"]) == 2

    def test_play_in_month(self, tmp_path: Path) -> None:
        path = tmp_path / "ref.csv"
        _write_reference(path, _sample_rows())
        result = _load_reference_by_month(path)

        assert "play_in" in result["2024-25"]
        assert "2025-04" in result["2024-25"]["play_in"]


# ---------------------------------------------------------------------------
# _load_reference_details
# ---------------------------------------------------------------------------


class TestLoadReferenceDetails:
    def test_returns_team_details(self, tmp_path: Path) -> None:
        path = tmp_path / "ref.csv"
        _write_reference(path, _sample_rows())
        details = _load_reference_details(path)

        assert ("2024-10-22", "BOS@NYK") in details
        d = details[("2024-10-22", "BOS@NYK")]
        assert d["away_team"] == "Boston Celtics"
        assert d["home_team"] == "New York Knicks"

    def test_covers_all_rows(self, tmp_path: Path) -> None:
        path = tmp_path / "ref.csv"
        _write_reference(path, _sample_rows())
        details = _load_reference_details(path)
        assert len(details) == len(_sample_rows())


# ---------------------------------------------------------------------------
# _pct helper
# ---------------------------------------------------------------------------


class TestPct:
    def test_basic(self) -> None:
        assert _pct(3, 4) == 75.0

    def test_zero_denominator(self) -> None:
        assert _pct(0, 0) == 0.0

    def test_rounding(self) -> None:
        assert _pct(1, 3) == 33.3


# ---------------------------------------------------------------------------
# query_observed_games / query_observed_games_with_all_available
# ---------------------------------------------------------------------------


class TestQueryObservedGames:
    def test_returns_flat_set(self) -> None:
        entries = [
            _make_entry(date(2024, 10, 22), "BOS@NYK"),
            _make_entry(date(2024, 10, 22), "LAL@GSW"),
        ]
        session = _make_db(entries)
        result = query_observed_games(session)
        assert isinstance(result, set)
        assert ("2024-10-22", "BOS@NYK") in result
        assert ("2024-10-22", "LAL@GSW") in result

    def test_only_player_type_entries(self) -> None:
        entries = [
            _make_entry(date(2024, 10, 22), "BOS@NYK", entry_type="player"),
            _make_entry(date(2024, 10, 23), "LAL@GSW", entry_type="all_available"),
            _make_entry(date(2024, 10, 24), "MIA@CHI", entry_type="not_submitted"),
        ]
        session = _make_db(entries)
        result = query_observed_games(session)
        assert ("2024-10-22", "BOS@NYK") in result
        assert ("2024-10-23", "LAL@GSW") not in result
        assert ("2024-10-24", "MIA@CHI") not in result

    def test_deduplicates(self) -> None:
        entries = [
            _make_entry(date(2024, 10, 22), "BOS@NYK", report_id=1, row_number=1),
            _make_entry(date(2024, 10, 22), "BOS@NYK", report_id=1, row_number=2),
        ]
        session = _make_db(entries)
        result = query_observed_games(session)
        assert len([e for e in result if e == ("2024-10-22", "BOS@NYK")]) == 1


class TestQueryAllAvailable:
    def test_only_all_available(self) -> None:
        entries = [
            _make_entry(date(2024, 10, 22), "BOS@NYK", entry_type="player"),
            _make_entry(date(2024, 10, 23), "LAL@GSW", entry_type="all_available"),
        ]
        session = _make_db(entries)
        result = query_observed_games_with_all_available(session)
        assert ("2024-10-22", "BOS@NYK") not in result
        assert ("2024-10-23", "LAL@GSW") in result


# ---------------------------------------------------------------------------
# audit integration tests (in-memory SQLite, no real database)
# ---------------------------------------------------------------------------


class TestAuditCoverageLogic:
    def _run_audit(
        self,
        ref_path: Path,
        entries: list[dict],
    ) -> dict[str, object]:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            for i, e in enumerate(entries):
                e = {**e, "row_number": i + 1}
                session.execute(
                    Base.metadata.tables["nba_report_entries"].insert().values(**e)
                )
            session.commit()

        from unittest.mock import patch

        with patch(
            "scripts.audit_schedule_coverage.get_settings"
        ) as mock_settings:
            mock_settings.return_value.database_url = str(engine.url)
            with patch("scripts.audit_schedule_coverage.create_engine") as mock_eng:
                mock_eng.return_value = engine
                return audit(ref_path, database_url=str(engine.url))

    def test_coverage_never_exceeds_100_percent(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref.csv"
        _write_reference(ref, _sample_rows())
        entries = [
            _make_entry(date(2024, 10, 22), "BOS@NYK"),
            _make_entry(date(2024, 10, 22), "LAL@GSW"),
            _make_entry(date(2024, 10, 8), "DEN@PHX"),
            _make_entry(date(2025, 4, 15), "MIA@CHI"),
            _make_entry(date(2025, 5, 1), "BOS@MIL"),
            _make_entry(date(2025, 10, 22), "OKC@DEN"),
        ]
        report = self._run_audit(ref, entries)
        for season_data in report["seasons"].values():
            for _st, metrics in season_data.items():
                assert metrics["player_observation_coverage_pct"] <= 100.0
                assert metrics["total_covered_pct"] <= 100.0

    def test_playoff_game_counts_under_playoffs_not_regular(
        self, tmp_path: Path
    ) -> None:
        ref = tmp_path / "ref.csv"
        _write_reference(ref, _sample_rows())
        entries = [
            _make_entry(date(2025, 5, 1), "BOS@MIL"),
        ]
        report = self._run_audit(ref, entries)
        season = report["seasons"]["2024-25"]
        assert season["playoffs"]["games_with_player_observations"] == 1
        assert season["regular"]["games_with_player_observations"] == 0

    def test_playin_game_counts_under_play_in(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref.csv"
        _write_reference(ref, _sample_rows())
        entries = [
            _make_entry(date(2025, 4, 15), "MIA@CHI"),
        ]
        report = self._run_audit(ref, entries)
        season = report["seasons"]["2024-25"]
        assert season["play_in"]["games_with_player_observations"] == 1
        assert season["regular"]["games_with_player_observations"] == 0

    def test_unmatched_canonical_games_do_not_inflate_coverage(
        self, tmp_path: Path
    ) -> None:
        ref = tmp_path / "ref.csv"
        rows = [
            {
                "season": "2024-25",
                "game_date": "2024-10-22",
                "season_type": "regular",
                "away_team": "Boston Celtics",
                "home_team": "New York Knicks",
                "matchup": "BOS@NYK",
            },
        ]
        _write_reference(ref, rows)
        entries = [
            _make_entry(date(2024, 10, 22), "BOS@NYK"),
            _make_entry(date(2024, 11, 15), "LAL@GSW"),
        ]
        report = self._run_audit(ref, entries)
        regular = report["seasons"]["2024-25"]["regular"]
        assert regular["scheduled_games"] == 1
        assert regular["games_with_player_observations"] == 1
        assert regular["player_observation_coverage_pct"] == 100.0
        assert regular["missing_games"] == 0

    def test_missing_equals_scheduled_minus_matched(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref.csv"
        rows = [
            {
                "season": "2024-25",
                "game_date": "2024-10-22",
                "season_type": "regular",
                "away_team": "Boston Celtics",
                "home_team": "New York Knicks",
                "matchup": "BOS@NYK",
            },
            {
                "season": "2024-25",
                "game_date": "2024-10-22",
                "season_type": "regular",
                "away_team": "Los Angeles Lakers",
                "home_team": "Golden State Warriors",
                "matchup": "LAL@GSW",
            },
        ]
        _write_reference(ref, rows)
        entries = [
            _make_entry(date(2024, 10, 22), "BOS@NYK"),
        ]
        report = self._run_audit(ref, entries)
        regular = report["seasons"]["2024-25"]["regular"]
        assert regular["scheduled_games"] == 2
        assert regular["games_with_player_observations"] == 1
        assert regular["total_covered_pct"] == 50.0
        assert regular["missing_games"] == 1
        assert regular["missing_game_keys"] == [("2024-10-22", "LAL@GSW")]

    def test_all_available_covers_without_player_observations(
        self, tmp_path: Path
    ) -> None:
        ref = tmp_path / "ref.csv"
        rows = [
            {
                "season": "2024-25",
                "game_date": "2024-10-22",
                "season_type": "regular",
                "away_team": "Boston Celtics",
                "home_team": "New York Knicks",
                "matchup": "BOS@NYK",
            },
        ]
        _write_reference(ref, rows)
        entries = [
            _make_entry(date(2024, 10, 22), "BOS@NYK", entry_type="all_available"),
        ]
        report = self._run_audit(ref, entries)
        regular = report["seasons"]["2024-25"]["regular"]
        assert regular["games_with_player_observations"] == 0
        assert regular["games_with_all_available"] == 1
        assert regular["total_covered_pct"] == 100.0
        assert regular["missing_games"] == 0

    def test_missing_details_populated_for_unmatched_games(
        self, tmp_path: Path
    ) -> None:
        ref = tmp_path / "ref.csv"
        rows = [
            {
                "season": "2024-25",
                "game_date": "2024-10-22",
                "season_type": "regular",
                "away_team": "Boston Celtics",
                "home_team": "New York Knicks",
                "matchup": "BOS@NYK",
            },
            {
                "season": "2024-25",
                "game_date": "2024-10-22",
                "season_type": "regular",
                "away_team": "Los Angeles Lakers",
                "home_team": "Golden State Warriors",
                "matchup": "LAL@GSW",
            },
        ]
        _write_reference(ref, rows)
        entries = [
            _make_entry(date(2024, 10, 22), "BOS@NYK"),
        ]
        report = self._run_audit(ref, entries)
        regular = report["seasons"]["2024-25"]["regular"]
        assert regular["missing_games"] == 1
        assert len(regular["missing_details"]) == 1
        d = regular["missing_details"][0]
        assert d["game_date"] == "2024-10-22"
        assert d["matchup"] == "LAL@GSW"
        assert d["away_team"] == "Los Angeles Lakers"
        assert d["home_team"] == "Golden State Warriors"

    def test_missing_details_empty_when_all_covered(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref.csv"
        rows = [
            {
                "season": "2024-25",
                "game_date": "2024-10-22",
                "season_type": "regular",
                "away_team": "Boston Celtics",
                "home_team": "New York Knicks",
                "matchup": "BOS@NYK",
            },
        ]
        _write_reference(ref, rows)
        entries = [
            _make_entry(date(2024, 10, 22), "BOS@NYK"),
        ]
        report = self._run_audit(ref, entries)
        regular = report["seasons"]["2024-25"]["regular"]
        assert regular["missing_games"] == 0
        assert regular["missing_details"] == []

    def test_missing_details_sorted_by_date_then_matchup(
        self, tmp_path: Path
    ) -> None:
        ref = tmp_path / "ref.csv"
        rows = [
            {
                "season": "2024-25",
                "game_date": "2024-11-01",
                "season_type": "regular",
                "away_team": "Miami Heat",
                "home_team": "Chicago Bulls",
                "matchup": "MIA@CHI",
            },
            {
                "season": "2024-25",
                "game_date": "2024-10-22",
                "season_type": "regular",
                "away_team": "Boston Celtics",
                "home_team": "New York Knicks",
                "matchup": "BOS@NYK",
            },
        ]
        _write_reference(ref, rows)
        report = self._run_audit(ref, [])
        regular = report["seasons"]["2024-25"]["regular"]
        assert regular["missing_games"] == 2
        dates = [d["game_date"] for d in regular["missing_details"]]
        assert dates == ["2024-10-22", "2024-11-01"]

    def test_missing_details_multiple_seasons(self, tmp_path: Path) -> None:
        ref = tmp_path / "ref.csv"
        _write_reference(ref, _sample_rows())
        entries = [
            _make_entry(date(2024, 10, 22), "BOS@NYK"),
            _make_entry(date(2025, 10, 22), "OKC@DEN"),
        ]
        report = self._run_audit(ref, entries)
        assert report["seasons"]["2024-25"]["regular"]["missing_games"] == 1
        assert report["seasons"]["2024-25"]["regular"]["missing_details"] == [
            {
                "game_date": "2024-10-22",
                "matchup": "LAL@GSW",
                "away_team": "Los Angeles Lakers",
                "home_team": "Golden State Warriors",
            }
        ]
        assert report["seasons"]["2025-26"]["regular"]["missing_games"] == 0
        assert report["seasons"]["2025-26"]["regular"]["missing_details"] == []
