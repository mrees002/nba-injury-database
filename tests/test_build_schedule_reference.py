"""Offline tests for scripts.build_schedule_reference."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from scripts.build_schedule_reference import (
    OUTPUT_COLUMNS,
    _load_csv,
    _load_json,
    _normalise_row,
    _parse_date,
    _resolve_abbr,
    _resolve_team_input,
    _validate_season_type,
    build_schedule,
    write_reference,
)

# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------


class TestParseDate:
    def test_iso_format(self) -> None:
        assert _parse_date("2024-10-22") == date(2024, 10, 22)

    def test_us_slash(self) -> None:
        assert _parse_date("10/22/2024") == date(2024, 10, 22)

    def test_us_dash(self) -> None:
        assert _parse_date("10-22-2024") == date(2024, 10, 22)

    def test_strips_whitespace(self) -> None:
        assert _parse_date("  2024-10-22  ") == date(2024, 10, 22)

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Unrecognised date format"):
            _parse_date("not-a-date")


# ---------------------------------------------------------------------------
# Team abbreviation resolution
# ---------------------------------------------------------------------------


class TestResolveAbbr:
    def test_known_team(self) -> None:
        assert _resolve_abbr("Boston Celtics") == "BOS"

    def test_uses_canonical_name(self) -> None:
        # "Los Angeles Clippers" -> "LA Clippers" -> "LAC"
        assert _resolve_abbr("Los Angeles Clippers") == "LAC"

    def test_unknown_team_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown team"):
            _resolve_abbr("Mars Missionaries")


# ---------------------------------------------------------------------------
# Team input resolution (abbreviations and full names)
# ---------------------------------------------------------------------------


class TestResolveTeamInput:
    def test_abbreviation_bos(self) -> None:
        assert _resolve_team_input("BOS") == "Boston Celtics"

    def test_abbreviation_lal(self) -> None:
        assert _resolve_team_input("LAL") == "Los Angeles Lakers"

    def test_abbreviation_gsw(self) -> None:
        assert _resolve_team_input("GSW") == "Golden State Warriors"

    def test_full_name(self) -> None:
        assert _resolve_team_input("Boston Celtics") == "Boston Celtics"

    def test_alias_resolves(self) -> None:
        assert _resolve_team_input("Los Angeles Clippers") == "LA Clippers"

    def test_abbreviation_strips_whitespace(self) -> None:
        assert _resolve_team_input("  BOS  ") == "Boston Celtics"

    def test_invalid_abbreviation_passthrough(self) -> None:
        assert _resolve_team_input("ZZZ") == "ZZZ"

    def test_invalid_name_passthrough(self) -> None:
        assert _resolve_team_input("Mars Missionaries") == "Mars Missionaries"


# ---------------------------------------------------------------------------
# Season type validation
# ---------------------------------------------------------------------------


class TestValidateSeasonType:
    @pytest.mark.parametrize("value", ["preseason", "regular", "play_in", "playoffs"])
    def test_valid(self, value: str) -> None:
        assert _validate_season_type(value) == value

    def test_normalises_whitespace(self) -> None:
        assert _validate_season_type("  Pre Season  ") == "preseason"

    def test_normalises_hyphens(self) -> None:
        assert _validate_season_type("play-in") == "play_in"

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid season_type"):
            _validate_season_type("all_star")


# ---------------------------------------------------------------------------
# Row normalisation
# ---------------------------------------------------------------------------


class TestNormaliseRow:
    def test_csv_style_columns(self) -> None:
        row = {
            "game_date": "2024-10-22",
            "away_team": "Boston Celtics",
            "home_team": "New York Knicks",
        }
        result = _normalise_row(row, default_season_type="regular")
        assert result["game_date"] == "2024-10-22"
        assert result["away_team"] == "Boston Celtics"
        assert result["home_team"] == "New York Knicks"
        assert result["matchup"] == "BOS@NYK"
        assert result["season"] == "2024-25"
        assert result["season_type"] == "regular"

    def test_json_style_columns(self) -> None:
        row = {"date": "2025-06-15", "away": "Los Angeles Lakers", "home": "Boston Celtics"}
        result = _normalise_row(row, default_season_type="playoffs")
        assert result["matchup"] == "LAL@BOS"
        assert result["season"] == "2024-25"
        assert result["season_type"] == "playoffs"

    def test_explicit_season_type_overrides_default(self) -> None:
        row = {
            "game_date": "2024-10-08",
            "away_team": "Denver Nuggets",
            "home_team": "Phoenix Suns",
            "season_type": "preseason",
        }
        result = _normalise_row(row, default_season_type="regular")
        assert result["season_type"] == "preseason"

    def test_missing_game_date_raises(self) -> None:
        row = {"away_team": "Boston Celtics", "home_team": "New York Knicks"}
        with pytest.raises(ValueError, match="Missing 'game_date'"):
            _normalise_row(row, default_season_type="regular")

    def test_missing_team_raises(self) -> None:
        row = {"game_date": "2024-10-22", "away_team": "Boston Celtics"}
        with pytest.raises(ValueError, match="Missing team"):
            _normalise_row(row, default_season_type="regular")

    def test_unknown_team_raises(self) -> None:
        row = {
            "game_date": "2024-10-22",
            "away_team": "Mars Missionaries",
            "home_team": "New York Knicks",
        }
        with pytest.raises(ValueError, match="Unknown team"):
            _normalise_row(row, default_season_type="regular")

    def test_no_season_type_raises(self) -> None:
        row = {
            "game_date": "2024-10-22",
            "away_team": "Boston Celtics",
            "home_team": "New York Knicks",
        }
        with pytest.raises(ValueError, match="No season_type"):
            _normalise_row(row, default_season_type=None)

    def test_abbreviation_away_team(self) -> None:
        row = {
            "game_date": "2024-10-22",
            "away_team": "BOS",
            "home_team": "New York Knicks",
        }
        result = _normalise_row(row, default_season_type="regular")
        assert result["away_team"] == "Boston Celtics"
        assert result["home_team"] == "New York Knicks"
        assert result["matchup"] == "BOS@NYK"

    def test_abbreviation_home_team(self) -> None:
        row = {
            "game_date": "2024-10-22",
            "away_team": "New York Knicks",
            "home_team": "LAL",
        }
        result = _normalise_row(row, default_season_type="regular")
        assert result["away_team"] == "New York Knicks"
        assert result["home_team"] == "Los Angeles Lakers"
        assert result["matchup"] == "NYK@LAL"

    def test_both_abbreviations(self) -> None:
        row = {
            "game_date": "2024-10-22",
            "away_team": "BOS",
            "home_team": "LAL",
        }
        result = _normalise_row(row, default_season_type="regular")
        assert result["away_team"] == "Boston Celtics"
        assert result["home_team"] == "Los Angeles Lakers"
        assert result["matchup"] == "BOS@LAL"

    def test_mixed_abbreviation_and_full_name(self) -> None:
        row = {
            "game_date": "2024-10-22",
            "away_team": "GSW",
            "home_team": "Denver Nuggets",
        }
        result = _normalise_row(row, default_season_type="regular")
        assert result["away_team"] == "Golden State Warriors"
        assert result["home_team"] == "Denver Nuggets"
        assert result["matchup"] == "GSW@DEN"

    def test_invalid_abbreviation_raises(self) -> None:
        row = {
            "game_date": "2024-10-22",
            "away_team": "ZZZ",
            "home_team": "New York Knicks",
        }
        with pytest.raises((ValueError, KeyError)):
            _normalise_row(row, default_season_type="regular")


# ---------------------------------------------------------------------------
# CSV / JSON loaders
# ---------------------------------------------------------------------------


class TestLoadCSV:
    def test_loads_valid_csv(self, tmp_path: Path) -> None:
        path = tmp_path / "sched.csv"
        path.write_text(
            "game_date,away_team,home_team\n2024-10-22,Boston Celtics,New York Knicks\n"
        )
        rows = _load_csv(path)
        assert len(rows) == 1
        assert rows[0]["away_team"] == "Boston Celtics"

    def test_empty_csv_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.csv"
        path.write_text("")
        with pytest.raises(ValueError, match="Empty CSV"):
            _load_csv(path)


class TestLoadJSON:
    def test_loads_list(self, tmp_path: Path) -> None:
        path = tmp_path / "sched.json"
        data = [
            {
                "game_date": "2024-10-22",
                "away_team": "Boston Celtics",
                "home_team": "New York Knicks",
            }
        ]
        path.write_text(json.dumps(data))
        rows = _load_json(path)
        assert len(rows) == 1

    def test_loads_dict_with_games_key(self, tmp_path: Path) -> None:
        path = tmp_path / "sched.json"
        data = {
            "games": [
                {
                    "game_date": "2024-10-22",
                    "away_team": "Boston Celtics",
                    "home_team": "New York Knicks",
                }
            ]
        }
        path.write_text(json.dumps(data))
        rows = _load_json(path)
        assert len(rows) == 1

    def test_non_list_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "sched.json"
        path.write_text(json.dumps({"unexpected": True}))
        with pytest.raises(ValueError, match="Expected a JSON list"):
            _load_json(path)


# ---------------------------------------------------------------------------
# build_schedule integration
# ---------------------------------------------------------------------------


class TestBuildSchedule:
    def test_single_csv(self, tmp_path: Path) -> None:
        path = tmp_path / "sched.csv"
        path.write_text(
            "game_date,away_team,home_team,season_type\n"
            "2024-10-22,Boston Celtics,New York Knicks,regular\n"
            "2024-10-23,Los Angeles Lakers,Golden State Warriors,regular\n"
        )
        rows = build_schedule([path])
        assert len(rows) == 2
        assert rows[0]["matchup"] == "BOS@NYK"
        assert rows[1]["matchup"] == "LAL@GSW"

    def test_multiple_files(self, tmp_path: Path) -> None:
        csv1 = tmp_path / "a.csv"
        csv1.write_text(
            "game_date,away_team,home_team,season_type\n"
            "2024-10-22,Boston Celtics,New York Knicks,regular\n"
        )
        csv2 = tmp_path / "b.csv"
        csv2.write_text(
            "game_date,away_team,home_team,season_type\n"
            "2024-10-23,Los Angeles Lakers,Golden State Warriors,regular\n"
        )
        rows = build_schedule([csv1, csv2])
        assert len(rows) == 2

    def test_duplicate_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "dup.csv"
        path.write_text(
            "game_date,away_team,home_team,season_type\n"
            "2024-10-22,Boston Celtics,New York Knicks,regular\n"
            "2024-10-22,Boston Celtics,New York Knicks,regular\n"
        )
        with pytest.raises(ValueError, match="Duplicate"):
            build_schedule([path])

    def test_output_sorted(self, tmp_path: Path) -> None:
        path = tmp_path / "unsorted.csv"
        path.write_text(
            "game_date,away_team,home_team,season_type\n"
            "2024-10-23,Los Angeles Lakers,Golden State Warriors,regular\n"
            "2024-10-22,Boston Celtics,New York Knicks,regular\n"
        )
        rows = build_schedule([path])
        assert rows[0]["game_date"] == "2024-10-22"
        assert rows[1]["game_date"] == "2024-10-23"


# ---------------------------------------------------------------------------
# write_reference
# ---------------------------------------------------------------------------


class TestWriteReference:
    def test_writes_valid_csv(self, tmp_path: Path) -> None:
        out = tmp_path / "ref.csv"
        rows = [
            {
                "season": "2024-25",
                "game_date": "2024-10-22",
                "season_type": "regular",
                "away_team": "Boston Celtics",
                "home_team": "New York Knicks",
                "matchup": "BOS@NYK",
            }
        ]
        write_reference(rows, out)
        assert out.exists()
        with out.open() as fh:
            reader = csv.DictReader(fh)
            assert reader.fieldnames == OUTPUT_COLUMNS
            data = list(reader)
            assert len(data) == 1
            assert data[0]["matchup"] == "BOS@NYK"
