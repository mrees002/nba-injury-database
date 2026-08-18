"""Offline tests for scripts.fetch_nba_schedule_reference."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.fetch_nba_schedule_reference import (
    Game,
    _deduplicate,
    _game_date_iso,
    _parse_matchup,
    _write_raw_csv,
    _write_reference_csv,
    main,
)

# ---------------------------------------------------------------------------
# _parse_matchup
# ---------------------------------------------------------------------------


class TestParseMatchup:
    def test_away_game(self) -> None:
        away, home = _parse_matchup("BOS @ NYK")
        assert away == "BOS"
        assert home == "NYK"

    def test_home_game(self) -> None:
        away, home = _parse_matchup("NYK vs. BOS")
        assert away == "BOS"
        assert home == "NYK"

    def test_strips_whitespace(self) -> None:
        away, home = _parse_matchup("  LAL @ GSW  ")
        assert away == "LAL"
        assert home == "GSW"

    def test_invalid_matchup_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse MATCHUP"):
            _parse_matchup("garbage input")


# ---------------------------------------------------------------------------
# _game_date_iso
# ---------------------------------------------------------------------------


class TestGameDateIso:
    def test_iso_passthrough(self) -> None:
        assert _game_date_iso("2024-10-22") == "2024-10-22"

    def test_us_slash(self) -> None:
        assert _game_date_iso("10/22/2024") == "2024-10-22"

    def test_strips_whitespace(self) -> None:
        assert _game_date_iso("  2024-10-22  ") == "2024-10-22"

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Unrecognised date format"):
            _game_date_iso("not-a-date")


# ---------------------------------------------------------------------------
# _deduplicate
# ---------------------------------------------------------------------------


def _make_team_row(
    game_id: str = "0022400061",
    team: str = "BOS",
    matchup: str = "BOS vs. NYK",
    game_date: str = "2024-10-22",
) -> dict[str, str]:
    return {
        "SEASON_ID": "22024",
        "TEAM_ABBREVIATION": team,
        "TEAM_NAME": f"{team} Team",
        "GAME_ID": game_id,
        "GAME_DATE": game_date,
        "MATCHUP": matchup,
        "WL": "W",
    }


class TestDeduplicate:
    def test_single_game_pair(self) -> None:
        rows = [
            _make_team_row(team="BOS", matchup="BOS vs. NYK"),
            _make_team_row(team="NYK", matchup="NYK @ BOS"),
        ]
        games = _deduplicate(rows, "2024-25", "Regular Season")
        assert len(games) == 1
        g = games[0]
        assert g.game_id == "0022400061"
        assert g.season == "2024-25"
        assert g.game_date == "2024-10-22"
        assert g.season_type == "regular"
        assert g.away_abbr == "NYK"
        assert g.home_abbr == "BOS"

    def test_multiple_games(self) -> None:
        rows = [
            _make_team_row(
                game_id="001", team="BOS", matchup="BOS vs. NYK", game_date="2024-10-22",
            ),
            _make_team_row(
                game_id="001", team="NYK", matchup="NYK @ BOS", game_date="2024-10-22",
            ),
            _make_team_row(
                game_id="002", team="LAL", matchup="LAL @ GSW", game_date="2024-10-23",
            ),
            _make_team_row(
                game_id="002", team="GSW", matchup="GSW vs. LAL", game_date="2024-10-23",
            ),
        ]
        games = _deduplicate(rows, "2024-25", "Playoffs")
        assert len(games) == 2
        assert all(g.season_type == "playoffs" for g in games)
        # Sorted by game_date then away_abbr
        assert games[0].away_abbr == "NYK"
        assert games[1].away_abbr == "LAL"

    def test_drops_non_nba_teams(self) -> None:
        rows = [
            _make_team_row(
                game_id="099", team="NZB", matchup="NZB @ UTA", game_date="2024-10-04",
            ),
            _make_team_row(
                game_id="099", team="UTA", matchup="UTA vs. NZB", game_date="2024-10-04",
            ),
        ]
        games = _deduplicate(rows, "2024-25", "Pre Season")
        assert games == []

    def test_drops_odd_row_count(self) -> None:
        rows = [
            _make_team_row(game_id="003", team="BOS", matchup="BOS vs. NYK"),
        ]
        games = _deduplicate(rows, "2024-25", "Regular Season")
        assert games == []

    def test_empty_input(self) -> None:
        assert _deduplicate([], "2024-25", "Regular Season") == []


# ---------------------------------------------------------------------------
# Raw CSV write
# ---------------------------------------------------------------------------


class TestWriteRawCsv:
    def test_writes_file(self, tmp_path: Path) -> None:
        rows = [_make_team_row(), _make_team_row(team="NYK", matchup="NYK @ BOS")]
        out = tmp_path / "raw.csv"
        _write_raw_csv(rows, out)
        assert out.exists()
        with out.open() as fh:
            data = list(csv.DictReader(fh))
        assert len(data) == 2
        assert data[0]["GAME_ID"] == "0022400061"

    def test_empty_rows_noop(self, tmp_path: Path) -> None:
        out = tmp_path / "empty.csv"
        _write_raw_csv([], out)
        assert not out.exists()


# ---------------------------------------------------------------------------
# Reference CSV write
# ---------------------------------------------------------------------------


class TestWriteReferenceCsv:
    def test_writes_correct_columns(self, tmp_path: Path) -> None:
        games = [
            Game(
                game_id="001",
                season="2024-25",
                game_date="2024-10-22",
                season_type="regular",
                away_abbr="NYK",
                home_abbr="BOS",
            ),
        ]
        out = tmp_path / "ref.csv"
        _write_reference_csv(games, out)
        with out.open() as fh:
            reader = csv.reader(fh)
            header = next(reader)
            row = next(reader)
        assert header == ["season", "game_date", "season_type", "away_team", "home_team"]
        assert row == ["2024-25", "2024-10-22", "regular", "NYK", "BOS"]


# ---------------------------------------------------------------------------
# Integration: main() with mocked API
# ---------------------------------------------------------------------------


def _make_df_rows(
    game_id: str = "0022400061",
    team_a: str = "BOS",
    team_b: str = "NYK",
    game_date: str = "2024-10-22",
) -> list[dict[str, str]]:
    """Return two team-level dicts mimicking LeagueGameLog output."""
    return [
        {
            "SEASON_ID": "22024",
            "TEAM_ABBREVIATION": team_a,
            "TEAM_NAME": f"{team_a} Team",
            "GAME_ID": game_id,
            "GAME_DATE": game_date,
            "MATCHUP": f"{team_a} vs. {team_b}",
            "WL": "W",
        },
        {
            "SEASON_ID": "22024",
            "TEAM_ABBREVIATION": team_b,
            "TEAM_NAME": f"{team_b} Team",
            "GAME_ID": game_id,
            "GAME_DATE": game_date,
            "MATCHUP": f"{team_b} @ {team_a}",
            "WL": "L",
        },
    ]


class TestMain:
    def test_full_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() writes raw CSVs and the normalised reference CSV."""
        source_dir = tmp_path / "source"
        output_csv = tmp_path / "ref.csv"

        # Patch constants and time.sleep
        monkeypatch.setattr(
            "scripts.fetch_nba_schedule_reference.SOURCE_DIR", source_dir,
        )
        monkeypatch.setattr(
            "scripts.fetch_nba_schedule_reference.OUTPUT_CSV", output_csv,
        )
        monkeypatch.setattr("scripts.fetch_nba_schedule_reference.time.sleep", lambda _: None)

        # Patch only 2 season/type combos to keep the test fast
        monkeypatch.setattr(
            "scripts.fetch_nba_schedule_reference.SEASONS", ["2024-25"],
        )
        monkeypatch.setattr(
            "scripts.fetch_nba_schedule_reference.SEASON_TYPES",
            ["Regular Season", "Playoffs"],
        )

        call_count = 0

        def fake_fetch(season: str, season_type: str) -> list[dict[str, str]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Regular Season: return one game
                return _make_df_rows()
            # Playoffs: return empty
            return []

        monkeypatch.setattr(
            "scripts.fetch_nba_schedule_reference._fetch_one", fake_fetch,
        )

        rc = main([])
        assert rc == 0

        # Reference CSV should exist with one game
        assert output_csv.exists()
        with output_csv.open() as fh:
            reader = csv.reader(fh)
            header = next(reader)
            rows = list(reader)
        assert header == ["season", "game_date", "season_type", "away_team", "home_team"]
        assert len(rows) == 1
        assert rows[0] == ["2024-25", "2024-10-22", "regular", "NYK", "BOS"]

        # Raw CSV for Regular Season should exist
        raw_regular = source_dir / "2024-25_regular_season.csv"
        assert raw_regular.exists()

        # Raw CSV for Playoffs should NOT exist (empty data → no file)
        raw_playoffs = source_dir / "2024-25_playoffs.csv"
        assert not raw_playoffs.exists()

    def test_empty_season_logs_and_continues(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """All empty seasons still produce an empty reference CSV."""
        source_dir = tmp_path / "source"
        output_csv = tmp_path / "ref.csv"

        monkeypatch.setattr(
            "scripts.fetch_nba_schedule_reference.SOURCE_DIR", source_dir,
        )
        monkeypatch.setattr(
            "scripts.fetch_nba_schedule_reference.OUTPUT_CSV", output_csv,
        )
        monkeypatch.setattr("scripts.fetch_nba_schedule_reference.time.sleep", lambda _: None)
        monkeypatch.setattr(
            "scripts.fetch_nba_schedule_reference.SEASONS", ["2025-26"],
        )
        monkeypatch.setattr(
            "scripts.fetch_nba_schedule_reference.SEASON_TYPES", ["PlayIn"],
        )
        monkeypatch.setattr(
            "scripts.fetch_nba_schedule_reference._fetch_one",
            lambda s, st: [],
        )

        rc = main([])
        assert rc == 0
        assert output_csv.exists()
        with output_csv.open() as fh:
            reader = csv.reader(fh)
            next(reader)  # skip header
            assert list(reader) == []
