"""Tests for app.services.fetch_nba_schedule_api.

All network calls are mocked — no live requests are made.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.services.fetch_nba_schedule_api import (
    NormalizedGame,
    _deduplicate_rows,
    _game_date_iso,
    _parse_matchup,
    detect_current_season,
    fetch_season_schedule,
    fetch_season_type,
    normalized_games_to_rows,
)

# ---------------------------------------------------------------------------
# detect_current_season
# ---------------------------------------------------------------------------


class TestDetectCurrentSeason:
    def test_october_returns_upcoming_season(self):
        assert detect_current_season(date(2025, 10, 15)) == "2025-26"

    def test_november_returns_current_season(self):
        assert detect_current_season(date(2025, 11, 1)) == "2025-26"

    def test_january_returns_current_season(self):
        assert detect_current_season(date(2026, 1, 15)) == "2025-26"

    def test_june_returns_current_season(self):
        assert detect_current_season(date(2026, 6, 15)) == "2025-26"

    def test_september_returns_upcoming_season(self):
        assert detect_current_season(date(2026, 9, 10)) == "2026-27"

    def test_august_returns_previous_season(self):
        assert detect_current_season(date(2026, 8, 1)) == "2025-26"


# ---------------------------------------------------------------------------
# _parse_matchup
# ---------------------------------------------------------------------------


class TestParseMatchup:
    def test_away_game(self):
        assert _parse_matchup("BOS @ NYK") == ("BOS", "NYK")

    def test_home_game(self):
        assert _parse_matchup("BOS vs. NYK") == ("NYK", "BOS")

    def test_whitespace_handling(self):
        assert _parse_matchup("  LAL  @  GSW  ") == ("LAL", "GSW")

    def test_invalid_returns_none(self):
        assert _parse_matchup("INVALID") is None
        assert _parse_matchup("BOS - NYK") is None


# ---------------------------------------------------------------------------
# _game_date_iso
# ---------------------------------------------------------------------------


class TestGameDateIso:
    def test_iso_format(self):
        assert _game_date_iso("2025-10-22") == "2025-10-22"

    def test_us_format(self):
        assert _game_date_iso("10/22/2025") == "2025-10-22"

    def test_strips_whitespace(self):
        assert _game_date_iso("  2025-10-22  ") == "2025-10-22"

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="Unrecognised date format"):
            _game_date_iso("not-a-date")


# ---------------------------------------------------------------------------
# _deduplicate_rows
# ---------------------------------------------------------------------------


def _make_api_row(
    game_id: str = "0022400001",
    team_abbr: str = "BOS",
    team_name: str = "Boston Celtics",
    game_date: str = "2024-10-22",
    matchup: str = "BOS @ NYK",
    wl: str = "W",
) -> dict:
    return {
        "SEASON_ID": 2,
        "TEAM_ABBREVIATION": team_abbr,
        "TEAM_NAME": team_name,
        "GAME_ID": game_id,
        "GAME_DATE": game_date,
        "MATCHUP": matchup,
        "WL": wl,
    }


class TestDeduplicateRows:
    def test_basic_dedup(self):
        rows = [
            _make_api_row(team_abbr="BOS", team_name="Boston Celtics", matchup="BOS @ NYK"),
            _make_api_row(team_abbr="NYK", team_name="New York Knicks", matchup="BOS @ NYK"),
        ]
        games = _deduplicate_rows(rows, "2024-25", "Regular Season")
        assert len(games) == 1
        assert games[0].away_team == "Boston Celtics"
        assert games[0].home_team == "New York Knicks"
        assert games[0].matchup == "BOS@NYK"
        assert games[0].season_type == "regular"

    def test_skips_single_team_rows(self):
        rows = [
            _make_api_row(game_id="001", team_abbr="BOS", matchup="BOS @ NYK"),
        ]
        games = _deduplicate_rows(rows, "2024-25", "Regular Season")
        assert len(games) == 0

    def test_skips_non_nba_teams(self):
        rows = [
            _make_api_row(game_id="001", team_abbr="BOS", matchup="BOS @ NYK"),
            _make_api_row(game_id="001", team_abbr="XXX", team_name="Unknown", matchup="BOS @ NYK"),
        ]
        games = _deduplicate_rows(rows, "2024-25", "Regular Season")
        assert len(games) == 0

    def test_multiple_games(self):
        rows = [
            _make_api_row(
                game_id="001", team_abbr="BOS", team_name="Boston Celtics",
                matchup="BOS @ NYK", game_date="2024-10-22",
            ),
            _make_api_row(
                game_id="001", team_abbr="NYK", team_name="New York Knicks",
                matchup="BOS @ NYK", game_date="2024-10-22",
            ),
            _make_api_row(
                game_id="002", team_abbr="LAL", team_name="Los Angeles Lakers",
                matchup="LAL @ GSW", game_date="2024-10-23",
            ),
            _make_api_row(
                game_id="002", team_abbr="GSW", team_name="Golden State Warriors",
                matchup="LAL @ GSW", game_date="2024-10-23",
            ),
        ]
        games = _deduplicate_rows(rows, "2024-25", "Regular Season")
        assert len(games) == 2
        assert games[0].game_date == "2024-10-22"
        assert games[1].game_date == "2024-10-23"

    def test_playoffs_season_type(self):
        rows = [
            _make_api_row(
                game_id="003", team_abbr="BOS", team_name="Boston Celtics",
                matchup="BOS vs. NYK", game_date="2025-05-10",
            ),
            _make_api_row(
                game_id="003", team_abbr="NYK", team_name="New York Knicks",
                matchup="BOS vs. NYK", game_date="2025-05-10",
            ),
        ]
        games = _deduplicate_rows(rows, "2024-25", "Playoffs")
        assert len(games) == 1
        assert games[0].season_type == "playoffs"
        assert games[0].home_team == "Boston Celtics"
        assert games[0].away_team == "New York Knicks"

    def test_play_in_season_type(self):
        rows = [
            _make_api_row(
                game_id="004", team_abbr="MIA", team_name="Miami Heat",
                matchup="MIA vs. CHI", game_date="2025-04-16",
            ),
            _make_api_row(
                game_id="004", team_abbr="CHI", team_name="Chicago Bulls",
                matchup="MIA vs. CHI", game_date="2025-04-16",
            ),
        ]
        games = _deduplicate_rows(rows, "2024-25", "PlayIn")
        assert len(games) == 1
        assert games[0].season_type == "play_in"

    def test_preseason_uses_preseason_season_detection(self):
        rows = [
            _make_api_row(
                game_id="005", team_abbr="BOS", team_name="Boston Celtics",
                matchup="BOS @ CHA", game_date="2024-10-05",
            ),
            _make_api_row(
                game_id="005", team_abbr="CHA", team_name="Charlotte Hornets",
                matchup="BOS @ CHA", game_date="2024-10-05",
            ),
        ]
        games = _deduplicate_rows(rows, "2024-25", "Pre Season")
        assert len(games) == 1
        assert games[0].season_type == "preseason"

    def test_row_order_independence_away_first(self):
        """Away team's row comes first – home/away must still be correct."""
        rows = [
            _make_api_row(
                game_id="100", team_abbr="BOS", team_name="Boston Celtics",
                matchup="BOS @ NYK", game_date="2025-10-22",
            ),
            _make_api_row(
                game_id="100", team_abbr="NYK", team_name="New York Knicks",
                matchup="NYK vs. BOS", game_date="2025-10-22",
            ),
        ]
        games = _deduplicate_rows(rows, "2025-26", "Regular Season")
        assert len(games) == 1
        assert games[0].away_team == "Boston Celtics"
        assert games[0].home_team == "New York Knicks"
        assert games[0].matchup == "BOS@NYK"

    def test_row_order_independence_home_first(self):
        """Home team's row comes first – home/away must still be correct."""
        rows = [
            _make_api_row(
                game_id="101", team_abbr="NYK", team_name="New York Knicks",
                matchup="NYK vs. BOS", game_date="2025-10-22",
            ),
            _make_api_row(
                game_id="101", team_abbr="BOS", team_name="Boston Celtics",
                matchup="BOS @ NYK", game_date="2025-10-22",
            ),
        ]
        games = _deduplicate_rows(rows, "2025-26", "Regular Season")
        assert len(games) == 1
        assert games[0].away_team == "Boston Celtics"
        assert games[0].home_team == "New York Knicks"
        assert games[0].matchup == "BOS@NYK"

    def test_cross_validate_matchup_vs_team_abbr(self):
        """GAME_ID whose MATCHUP doesn't match TEAM_ABBREVIATIONs is skipped."""
        rows = [
            _make_api_row(
                game_id="bad", team_abbr="BOS", team_name="Boston Celtics",
                matchup="BOS @ CHI", game_date="2025-10-22",
            ),
            _make_api_row(
                game_id="bad", team_abbr="NYK", team_name="New York Knicks",
                matchup="NYK vs. CHI", game_date="2025-10-22",
            ),
        ]
        games = _deduplicate_rows(rows, "2025-26", "Regular Season")
        assert len(games) == 0

    def test_each_game_id_derived_own_rows_only(self):
        """Two different GAME_IDs must never bleed into each other."""
        rows = [
            _make_api_row(
                game_id="G1", team_abbr="BOS", team_name="Boston Celtics",
                matchup="BOS @ NYK", game_date="2025-10-22",
            ),
            _make_api_row(
                game_id="G1", team_abbr="NYK", team_name="New York Knicks",
                matchup="NYK vs. BOS", game_date="2025-10-22",
            ),
            _make_api_row(
                game_id="G2", team_abbr="LAL", team_name="Los Angeles Lakers",
                matchup="LAL @ GSW", game_date="2025-10-22",
            ),
            _make_api_row(
                game_id="G2", team_abbr="GSW", team_name="Golden State Warriors",
                matchup="GSW vs. LAL", game_date="2025-10-22",
            ),
        ]
        games = _deduplicate_rows(rows, "2025-26", "Regular Season")
        assert len(games) == 2
        matchUps = {g.matchup for g in games}
        assert matchUps == {"BOS@NYK", "LAL@GSW"}


# ---------------------------------------------------------------------------
# Regression: impossible extra games must never be produced
# ---------------------------------------------------------------------------


class TestImpossibleExtraGames:
    """The live sync once produced extra rows for games that don't exist.

    These four concrete examples must never appear as phantom rows when the
    input data does not contain them.
    """

    @staticmethod
    def _rows_for_game(game_id: str, team_a: str, team_b: str, date: str) -> list[dict]:
        """Return two realistic NBA API rows for team_a @ team_b on *date*."""
        return [
            _make_api_row(
                game_id=game_id, team_abbr=team_a, team_name=f"{team_a} Team",
                matchup=f"{team_a} @ {team_b}", game_date=date,
            ),
            _make_api_row(
                game_id=game_id, team_abbr=team_b, team_name=f"{team_b} Team",
                matchup=f"{team_b} vs. {team_a}", game_date=date,
            ),
        ]

    def test_2025_10_22_bos_nyk_not_extra(self):
        rows = self._rows_for_game("G001", "BOS", "NYK", "2025-10-22")
        games = _deduplicate_rows(rows, "2025-26", "Regular Season")
        assert len(games) == 1
        assert games[0].matchup == "BOS@NYK"
        assert games[0].game_date == "2025-10-22"

    def test_2025_10_22_lal_gsw_not_extra(self):
        rows = self._rows_for_game("G002", "LAL", "GSW", "2025-10-22")
        games = _deduplicate_rows(rows, "2025-26", "Regular Season")
        assert len(games) == 1
        assert games[0].matchup == "LAL@GSW"
        assert games[0].game_date == "2025-10-22"

    def test_2025_10_23_den_phx_not_extra(self):
        rows = self._rows_for_game("G003", "DEN", "PHX", "2025-10-23")
        games = _deduplicate_rows(rows, "2025-26", "Regular Season")
        assert len(games) == 1
        assert games[0].matchup == "DEN@PHX"
        assert games[0].game_date == "2025-10-23"

    def test_2026_05_10_bos_nyk_not_extra(self):
        rows = self._rows_for_game("G004", "BOS", "NYK", "2026-05-10")
        games = _deduplicate_rows(rows, "2025-26", "Playoffs")
        assert len(games) == 1
        assert games[0].matchup == "BOS@NYK"
        assert games[0].game_date == "2026-05-10"
        assert games[0].season_type == "playoffs"

    def test_no_double_counting_across_season_types(self):
        """Same game in Pre Season + Regular Season queries must not produce two rows."""
        from app.services.fetch_nba_schedule_api import fetch_season_schedule

        preseason_rows = [
            _make_api_row(
                game_id="PRE001", team_abbr="BOS", team_name="Boston Celtics",
                matchup="BOS @ NYK", game_date="2025-10-22",
            ),
            _make_api_row(
                game_id="PRE001", team_abbr="NYK", team_name="New York Knicks",
                matchup="NYK vs. BOS", game_date="2025-10-22",
            ),
        ]
        regular_rows = [
            _make_api_row(
                game_id="REG001", team_abbr="BOS", team_name="Boston Celtics",
                matchup="BOS @ NYK", game_date="2025-10-22",
            ),
            _make_api_row(
                game_id="REG001", team_abbr="NYK", team_name="New York Knicks",
                matchup="NYK vs. BOS", game_date="2025-10-22",
            ),
        ]

        def mock_fetch(season, st, **kwargs):
            if st == "Pre Season":
                return preseason_rows
            if st == "Regular Season":
                return regular_rows
            return []

        with patch(
            "app.services.fetch_nba_schedule_api.fetch_season_type",
            side_effect=mock_fetch,
        ), patch("app.services.fetch_nba_schedule_api.time.sleep"):
            games = fetch_season_schedule(
                "2025-26",
                ["Pre Season", "Regular Season"],
                delay_between_requests=0,
            )

        bos_nyk = [g for g in games if g.matchup == "BOS@NYK"]
        assert len(bos_nyk) == 1, (
            f"Expected exactly one BOS@NYK game, got {len(bos_nyk)}: {bos_nyk}"
        )


# ---------------------------------------------------------------------------
# fetch_season_type (mocked LeagueGameLog)
# ---------------------------------------------------------------------------


def _make_api_response(rows: list[dict]) -> pd.DataFrame:
    """Build a fake DataFrame matching the columns LeagueGameLog returns."""
    columns = [
        "SEASON_ID", "TEAM_ABBREVIATION", "TEAM_NAME",
        "GAME_ID", "GAME_DATE", "MATCHUP", "WL",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    data = [
        [r.get("SEASON_ID", 2), r["TEAM_ABBREVIATION"], r["TEAM_NAME"],
         r["GAME_ID"], r["GAME_DATE"], r["MATCHUP"], r.get("WL", "W")]
        for r in rows
    ]
    return pd.DataFrame(data, columns=columns)


def _mock_league_game_log(df: pd.DataFrame) -> MagicMock:
    """Return a mock LeagueGameLog whose .league_game_log.get_data_frame() yields *df*."""
    mock_lg = MagicMock()
    mock_lg.league_game_log.get_data_frame.return_value = df
    return mock_lg


class TestFetchSeasonType:
    def test_successful_fetch(self):
        rows = [
            _make_api_row(game_id="001", team_abbr="BOS", matchup="BOS @ NYK"),
            _make_api_row(game_id="001", team_abbr="NYK", matchup="BOS @ NYK"),
        ]
        df = _make_api_response(rows)

        with patch(
            "app.services.fetch_nba_schedule_api.LeagueGameLog",
            return_value=_mock_league_game_log(df),
        ):
            result = fetch_season_type("2024-25", "Regular Season")

        assert len(result) == 2
        assert result[0]["TEAM_ABBREVIATION"] == "BOS"

    def test_empty_dataframe(self):
        df = _make_api_response([])

        with patch(
            "app.services.fetch_nba_schedule_api.LeagueGameLog",
            return_value=_mock_league_game_log(df),
        ):
            result = fetch_season_type("2024-25", "Pre Season")

        assert result == []

    def test_retry_on_failure(self):
        good_df = _make_api_response([])
        good_mock = _mock_league_game_log(good_df)

        call_count = [0]

        def side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ConnectionError("timeout")
            return good_mock

        with patch(
            "app.services.fetch_nba_schedule_api.LeagueGameLog",
            side_effect=side_effect,
        ), patch("app.services.fetch_nba_schedule_api.time.sleep"):
            result = fetch_season_type(
                "2024-25", "Regular Season",
                max_retries=2, backoff=0.01,
            )
        assert result == []
        assert call_count[0] == 2

    def test_all_retries_fail_raises(self):
        with patch(
            "app.services.fetch_nba_schedule_api.LeagueGameLog",
            side_effect=ConnectionError("timeout"),
        ), patch("app.services.fetch_nba_schedule_api.time.sleep"):
            with pytest.raises(RuntimeError, match="Failed to fetch"):
                fetch_season_type(
                    "2024-25", "Regular Season",
                    max_retries=2, backoff=0.01,
                )


# ---------------------------------------------------------------------------
# fetch_season_schedule (mocked fetch_season_type)
# ---------------------------------------------------------------------------


class TestFetchSeasonSchedule:
    def test_merges_multiple_season_types(self):
        rows_reg = [
            _make_api_row(game_id="001", team_abbr="BOS", matchup="BOS @ NYK",
                          game_date="2024-10-22"),
            _make_api_row(game_id="001", team_abbr="NYK", matchup="BOS @ NYK",
                          game_date="2024-10-22"),
        ]
        rows_playoffs = [
            _make_api_row(game_id="002", team_abbr="BOS", matchup="BOS vs. NYK",
                          game_date="2025-05-10"),
            _make_api_row(game_id="002", team_abbr="NYK", matchup="BOS vs. NYK",
                          game_date="2025-05-10"),
        ]

        def mock_fetch(season, st, **kwargs):
            if st == "Regular Season":
                return rows_reg
            elif st == "Playoffs":
                return rows_playoffs
            return []

        with patch(
            "app.services.fetch_nba_schedule_api.fetch_season_type", side_effect=mock_fetch
        ), patch("app.services.fetch_nba_schedule_api.time.sleep"):
            games = fetch_season_schedule(
                "2024-25",
                ["Regular Season", "Playoffs"],
                delay_between_requests=0,
            )

        assert len(games) == 2
        types = {g.season_type for g in games}
        assert types == {"regular", "playoffs"}

    def test_auto_detect_season_types(self):
        with patch(
            "app.services.fetch_nba_schedule_api.fetch_season_type", return_value=[]
        ), patch("app.services.fetch_nba_schedule_api.time.sleep"):
            # Should not raise — defaults to all four season types
            games = fetch_season_schedule("2024-25")
        assert isinstance(games, list)


# ---------------------------------------------------------------------------
# normalized_games_to_rows
# ---------------------------------------------------------------------------


class TestNormalizedGamesToRows:
    def test_conversion(self):
        games = [
            NormalizedGame(
                season="2024-25",
                game_date="2024-10-22",
                season_type="regular",
                away_team="Boston Celtics",
                home_team="New York Knicks",
                matchup="BOS@NYK",
            ),
        ]
        rows = normalized_games_to_rows(games)
        assert len(rows) == 1
        assert rows[0] == {
            "season": "2024-25",
            "game_date": "2024-10-22",
            "season_type": "regular",
            "away_team": "Boston Celtics",
            "home_team": "New York Knicks",
            "matchup": "BOS@NYK",
        }

    def test_empty_list(self):
        assert normalized_games_to_rows([]) == []
