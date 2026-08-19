"""Offline tests for scripts.audit_balldontlie_schedule.

Tests normalization, comparison, and helper functions without network or DB access.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from scripts.audit_balldontlie_schedule import (
    BDLGame,
    build_bdl_lookup,
    build_ref_lookup,
    canonical_team_name,
    compare,
    load_reference_games,
    normalize_bdl_game,
)

# ---------------------------------------------------------------------------
# canonical_team_name
# ---------------------------------------------------------------------------

class TestCanonicalTeamName:
    def test_normal_abbreviation_passthrough(self) -> None:
        assert canonical_team_name("Boston Celtics") == "Boston Celtics"

    def test_clippers_alias(self) -> None:
        assert canonical_team_name("Los Angeles Clippers") == "LA Clippers"

    def test_whitespace_normalization(self) -> None:
        assert canonical_team_name("  Boston   Celtics  ") == "Boston Celtics"

    def test_unknown_name_passthrough(self) -> None:
        assert canonical_team_name("Some Unknown Team") == "Some Unknown Team"


# ---------------------------------------------------------------------------
# normalize_bdl_game
# ---------------------------------------------------------------------------

class TestNormalizeBdlGame:
    def _make_raw(
        self,
        *,
        game_id: int = 1001,
        date_str: str = "2025-10-22",
        home_abbr: str = "NYK",
        away_abbr: str = "BOS",
        postseason: bool = False,
    ) -> dict:
        return {
            "id": game_id,
            "date": date_str,
            "season": 2025,
            "status": "Final",
            "postseason": postseason,
            "home_team": {"abbreviation": home_abbr, "full_name": "New York Knicks"},
            "visitor_team": {"abbreviation": away_abbr, "full_name": "Boston Celtics"},
        }

    def test_basic_normalization(self) -> None:
        raw = self._make_raw()
        game = normalize_bdl_game(raw)
        assert game is not None
        assert game.bdl_id == 1001
        assert game.game_date == date(2025, 10, 22)
        assert game.home_abbr == "NYK"
        assert game.away_abbr == "BOS"
        assert game.matchup == "BOS@NYK"
        assert game.postseason is False

    def test_postseason_true(self) -> None:
        raw = self._make_raw(postseason=True)
        game = normalize_bdl_game(raw)
        assert game is not None
        assert game.postseason is True

    def test_unknown_home_team_returns_none(self) -> None:
        raw = self._make_raw(home_abbr="XYZ")
        assert normalize_bdl_game(raw) is None

    def test_unknown_away_team_returns_none(self) -> None:
        raw = self._make_raw(away_abbr="XYZ")
        assert normalize_bdl_game(raw) is None

    def test_missing_home_team_returns_none(self) -> None:
        raw = {"id": 1, "date": "2025-10-22"}
        assert normalize_bdl_game(raw) is None


# ---------------------------------------------------------------------------
# build_ref_lookup / build_bdl_lookup
# ---------------------------------------------------------------------------

class TestLookups:
    def test_build_ref_lookup(self) -> None:
        rows = [
            {"game_date": "2025-10-22", "matchup": "BOS@NYK", "season": "2025-26",
             "season_type": "regular", "away_team": "Boston Celtics",
             "home_team": "New York Knicks"},
        ]
        lookup = build_ref_lookup(rows)
        assert ("2025-10-22", "BOS@NYK") in lookup
        assert lookup[("2025-10-22", "BOS@NYK")]["season_type"] == "regular"

    def test_build_bdl_lookup(self) -> None:
        games = [
            BDLGame(bdl_id=1, game_date=date(2025, 10, 22), away_abbr="BOS",
                    home_abbr="NYK", matchup="BOS@NYK", postseason=False),
        ]
        lookup = build_bdl_lookup(games)
        assert ("2025-10-22", "BOS@NYK") in lookup
        assert lookup[("2025-10-22", "BOS@NYK")].bdl_id == 1


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------

class TestCompare:
    def _make_ref_row(self, game_date: str, matchup: str, season_type: str) -> dict:
        return {
            "season": "2025-26",
            "game_date": game_date,
            "season_type": season_type,
            "away_team": "Boston Celtics",
            "home_team": "New York Knicks",
            "matchup": matchup,
        }

    def _make_bdl_game(
        self, game_date: date, matchup: str, postseason: bool = False,
    ) -> BDLGame:
        parts = matchup.split("@")
        return BDLGame(
            bdl_id=hash(matchup) % 100000,
            game_date=game_date,
            away_abbr=parts[0],
            home_abbr=parts[1],
            matchup=matchup,
            postseason=postseason,
        )

    def test_perfect_match(self) -> None:
        ref_rows = [self._make_ref_row("2025-10-22", "BOS@NYK", "regular")]
        bdl_games = [self._make_bdl_game(date(2025, 10, 22), "BOS@NYK")]
        report = compare(ref_rows, bdl_games)
        assert report["exact_matches"] == 1
        assert report["missing_from_bdl"] == []
        assert report["extra_in_bdl"] == []

    def test_missing_from_bdl(self) -> None:
        ref_rows = [self._make_ref_row("2025-10-22", "BOS@NYK", "regular")]
        bdl_games = []
        report = compare(ref_rows, bdl_games)
        assert report["exact_matches"] == 0
        assert report["missing_from_bdl"] == [("2025-10-22", "BOS@NYK")]

    def test_extra_in_bdl(self) -> None:
        ref_rows = []
        bdl_games = [self._make_bdl_game(date(2025, 10, 22), "BOS@NYK")]
        report = compare(ref_rows, bdl_games)
        assert report["exact_matches"] == 0
        assert report["extra_in_bdl"] == [("2025-10-22", "BOS@NYK")]

    def test_postseason_breakdown(self) -> None:
        ref_rows = [
            self._make_ref_row("2026-04-18", "BOS@NYK", "playoffs"),
            self._make_ref_row("2025-10-22", "BOS@NYK", "regular"),
        ]
        bdl_games = [
            self._make_bdl_game(date(2026, 4, 18), "BOS@NYK", postseason=True),
            self._make_bdl_game(date(2025, 10, 22), "BOS@NYK", postseason=False),
        ]
        report = compare(ref_rows, bdl_games)
        assert report["postseason_by_type"]["playoffs"]["postseason=true"] == 1
        assert report["postseason_by_type"]["regular"]["postseason=false"] == 1

    def test_playin_games_captured(self) -> None:
        ref_rows = [
            self._make_ref_row("2026-04-14", "MIA@CHA", "play_in"),
        ]
        bdl_games = [
            self._make_bdl_game(date(2026, 4, 14), "MIA@CHA", postseason=True),
        ]
        report = compare(ref_rows, bdl_games)
        assert len(report["playin_games"]) == 1
        assert report["playin_games"][0]["bdl_postseason"] == "True"

    def test_preseason_games_captured(self) -> None:
        ref_rows = [
            self._make_ref_row("2025-10-02", "NYK@PHI", "preseason"),
        ]
        bdl_games = [
            self._make_bdl_game(date(2025, 10, 2), "NYK@PHI", postseason=False),
        ]
        report = compare(ref_rows, bdl_games)
        assert len(report["preseason_games"]) == 1
        assert report["preseason_games"][0]["bdl_postseason"] == "False"


# ---------------------------------------------------------------------------
# load_reference_games (offline, using real CSV)
# ---------------------------------------------------------------------------

class TestLoadReferenceGames:
    REAL_CSV = (
        Path(__file__).resolve().parent.parent / "data" / "reference" / "nba_schedule_games.csv"
    )

    @pytest.mark.skipif(not REAL_CSV.exists(), reason="reference CSV not found")
    def test_loads_2025_26_season(self) -> None:
        rows = load_reference_games(self.REAL_CSV, "2025-26")
        assert len(rows) == 1386
        assert all(r["season"] == "2025-26" for r in rows)

    @pytest.mark.skipif(not REAL_CSV.exists(), reason="reference CSV not found")
    def test_no_other_seasons_leak(self) -> None:
        rows = load_reference_games(self.REAL_CSV, "2025-26")
        seasons = {r["season"] for r in rows}
        assert seasons == {"2025-26"}
