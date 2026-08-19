"""Tests for the sync-live subcommand of sync_nba_schedule.

All network calls are mocked — no live requests are made.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.jobs.sync_nba_schedule import (
    build_parser,
    main,
)
from app.models.nba import NBAScheduleGame
from app.services.fetch_nba_schedule_api import NormalizedGame, SEASON_TYPES_API

_API_MODULE = "app.services.fetch_nba_schedule_api"

SAMPLE_API_GAMES = [
    NormalizedGame(
        season="2025-26",
        game_date="2025-10-22",
        season_type="regular",
        away_team="Boston Celtics",
        home_team="New York Knicks",
        matchup="BOS@NYK",
    ),
    NormalizedGame(
        season="2025-26",
        game_date="2025-10-22",
        season_type="regular",
        away_team="Los Angeles Lakers",
        home_team="Golden State Warriors",
        matchup="LAL@GSW",
    ),
    NormalizedGame(
        season="2025-26",
        game_date="2025-10-23",
        season_type="regular",
        away_team="Denver Nuggets",
        home_team="Phoenix Suns",
        matchup="DEN@PHX",
    ),
]


@pytest.fixture
def engine(tmp_path):
    database_engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(database_engine)
    try:
        yield database_engine
    finally:
        database_engine.dispose()


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestSyncLiveParser:
    def test_sync_live_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["sync-live"])
        assert args.command == "sync-live"
        assert args.season is None
        assert args.source == "nba_stats_api"
        assert args.dry_run is False
        assert args.season_types is None
        assert args.timeout == 30.0

    def test_sync_live_with_season(self):
        parser = build_parser()
        args = parser.parse_args(["sync-live", "--season", "2024-25"])
        assert args.season == "2024-25"

    def test_sync_live_dry_run(self):
        parser = build_parser()
        args = parser.parse_args(["sync-live", "--dry-run"])
        assert args.dry_run is True

    def test_sync_live_custom_source(self):
        parser = build_parser()
        args = parser.parse_args(["sync-live", "--source", "manual"])
        assert args.source == "manual"

    def test_sync_live_season_types(self):
        parser = build_parser()
        args = parser.parse_args([
            "sync-live",
            "--season-type", "Regular Season",
            "--season-type", "Playoffs",
        ])
        assert args.season_types == ["Regular Season", "Playoffs"]

    def test_sync_live_custom_timeout(self):
        parser = build_parser()
        args = parser.parse_args(["sync-live", "--timeout", "60"])
        assert args.timeout == 60.0


# ---------------------------------------------------------------------------
# End-to-end sync-live (mocked network, real DB)
# ---------------------------------------------------------------------------


class TestSyncLiveIntegration:
    def test_sync_live_inserts_games(self, engine):
        with patch(
            f"{_API_MODULE}.fetch_season_schedule",
            return_value=SAMPLE_API_GAMES,
        ), patch(
            f"{_API_MODULE}.detect_current_season",
            return_value="2025-26",
        ), patch(
            "app.jobs.sync_nba_schedule.build_engine",
            return_value=engine,
        ):
            result = main(["sync-live", "--season", "2025-26"])

        assert result == 0
        with Session(engine, expire_on_commit=False) as session:
            count = session.scalar(select(func.count()).select_from(NBAScheduleGame))
            assert count == 3

            game = session.scalar(
                select(NBAScheduleGame).where(NBAScheduleGame.matchup == "BOS@NYK")
            )
            assert game.season == "2025-26"
            assert game.game_date == date(2025, 10, 22)
            assert game.season_type == "regular"
            assert game.away_team == "Boston Celtics"
            assert game.home_team == "New York Knicks"
            assert game.source == "nba_stats_api"

    def test_sync_live_is_idempotent(self, engine):
        with patch(
            f"{_API_MODULE}.fetch_season_schedule",
            return_value=SAMPLE_API_GAMES,
        ), patch(
            f"{_API_MODULE}.detect_current_season",
            return_value="2025-26",
        ), patch(
            "app.jobs.sync_nba_schedule.build_engine",
            return_value=engine,
        ):
            for _ in range(2):
                main(["sync-live", "--season", "2025-26"])

        with Session(engine, expire_on_commit=False) as session:
            count = session.scalar(select(func.count()).select_from(NBAScheduleGame))
            assert count == 3

    def test_sync_live_picks_up_new_playoff_games(self, engine):
        regular_games = [
            NormalizedGame(
                season="2025-26",
                game_date="2025-10-22",
                season_type="regular",
                away_team="Boston Celtics",
                home_team="New York Knicks",
                matchup="BOS@NYK",
            ),
        ]
        playoff_games = regular_games + [
            NormalizedGame(
                season="2025-26",
                game_date="2026-05-10",
                season_type="playoffs",
                away_team="Boston Celtics",
                home_team="New York Knicks",
                matchup="BOS@NYK",
            ),
        ]

        call_count = [0]

        def mock_fetch(season, types, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return regular_games
            return playoff_games

        with patch(
            f"{_API_MODULE}.fetch_season_schedule",
            side_effect=mock_fetch,
        ), patch(
            f"{_API_MODULE}.detect_current_season",
            return_value="2025-26",
        ), patch(
            "app.jobs.sync_nba_schedule.build_engine",
            return_value=engine,
        ):
            main(["sync-live", "--season", "2025-26"])
            main(["sync-live", "--season", "2025-26"])

        with Session(engine, expire_on_commit=False) as session:
            count = session.scalar(select(func.count()).select_from(NBAScheduleGame))
            assert count == 2

    def test_sync_live_dry_run_does_not_write(self, engine):
        with patch(
            f"{_API_MODULE}.fetch_season_schedule",
            return_value=SAMPLE_API_GAMES,
        ), patch(
            f"{_API_MODULE}.detect_current_season",
            return_value="2025-26",
        ), patch(
            "app.jobs.sync_nba_schedule.build_engine",
            return_value=engine,
        ):
            result = main(["sync-live", "--season", "2025-26", "--dry-run"])

        assert result == 0
        with Session(engine, expire_on_commit=False) as session:
            count = session.scalar(select(func.count()).select_from(NBAScheduleGame))
            assert count == 0

    def test_sync_live_with_specific_season_types(self, engine):
        playoff_games = [
            NormalizedGame(
                season="2025-26",
                game_date="2026-05-10",
                season_type="playoffs",
                away_team="Boston Celtics",
                home_team="New York Knicks",
                matchup="BOS@NYK",
            ),
        ]

        with patch(
            f"{_API_MODULE}.fetch_season_schedule",
            return_value=playoff_games,
        ), patch(
            "app.jobs.sync_nba_schedule.build_engine",
            return_value=engine,
        ):
            result = main([
                "sync-live", "--season", "2025-26",
                "--season-type", "Playoffs",
            ])

        assert result == 0
        with Session(engine, expire_on_commit=False) as session:
            count = session.scalar(select(func.count()).select_from(NBAScheduleGame))
            assert count == 1

            game = session.scalar(select(NBAScheduleGame))
            assert game.season_type == "playoffs"
            assert game.source == "nba_stats_api"

    def test_sync_live_auto_detects_season(self, engine):
        with patch(
            f"{_API_MODULE}.fetch_season_schedule",
            return_value=SAMPLE_API_GAMES,
        ), patch(
            f"{_API_MODULE}.detect_current_season",
            return_value="2025-26",
        ), patch(
            "app.jobs.sync_nba_schedule.build_engine",
            return_value=engine,
        ):
            result = main(["sync-live"])

        assert result == 0
        with Session(engine, expire_on_commit=False) as session:
            count = session.scalar(select(func.count()).select_from(NBAScheduleGame))
            assert count == 3


# ---------------------------------------------------------------------------
# Default season-type behavior
# ---------------------------------------------------------------------------


class TestDefaultSeasonTypes:
    """Verify that sync-live defaults to all four season types."""

    def test_default_fetches_all_four_types(self, engine):
        """When --season-type is omitted, fetch_season_schedule receives all four types."""
        captured_types = []

        def mock_fetch(season, types, **kwargs):
            captured_types.extend(types)
            return []

        with patch(
            f"{_API_MODULE}.fetch_season_schedule",
            side_effect=mock_fetch,
        ), patch(
            f"{_API_MODULE}.detect_current_season",
            return_value="2025-26",
        ), patch(
            "app.jobs.sync_nba_schedule.build_engine",
            return_value=engine,
        ):
            main(["sync-live", "--season", "2025-26"])

        assert captured_types == list(SEASON_TYPES_API)

    def test_explicit_season_type_fetches_only_requested(self, engine):
        """When --season-type is given, only those types are fetched."""
        captured_types = []

        def mock_fetch(season, types, **kwargs):
            captured_types.extend(types)
            return []

        with patch(
            f"{_API_MODULE}.fetch_season_schedule",
            side_effect=mock_fetch,
        ), patch(
            f"{_API_MODULE}.detect_current_season",
            return_value="2025-26",
        ), patch(
            "app.jobs.sync_nba_schedule.build_engine",
            return_value=engine,
        ):
            main([
                "sync-live", "--season", "2025-26",
                "--season-type", "Playoffs",
            ])

        assert captured_types == ["Playoffs"]

    def test_multiple_explicit_season_types(self, engine):
        """Multiple --season-type flags pass all requested types."""
        captured_types = []

        def mock_fetch(season, types, **kwargs):
            captured_types.extend(types)
            return []

        with patch(
            f"{_API_MODULE}.fetch_season_schedule",
            side_effect=mock_fetch,
        ), patch(
            f"{_API_MODULE}.detect_current_season",
            return_value="2025-26",
        ), patch(
            "app.jobs.sync_nba_schedule.build_engine",
            return_value=engine,
        ):
            main([
                "sync-live", "--season", "2025-26",
                "--season-type", "Regular Season",
                "--season-type", "PlayIn",
            ])

        assert captured_types == ["Regular Season", "PlayIn"]


# ---------------------------------------------------------------------------
# No duplicates across season types
# ---------------------------------------------------------------------------


class TestNoDuplicates:
    """Ensure same game across multiple season types doesn't produce duplicate rows.

    Deduplication across season types happens inside fetch_season_schedule().
    The upsert layer expects already-deduplicated input. These tests verify
    the end-to-end pipeline: fetch_season_schedule returns deduplicated data,
    and upsertSchedule_rows correctly inserts it.
    """

    def test_no_duplicates_across_season_types(self, engine):
        """fetch_season_schedule deduplicates; upsert receives one row per game."""
        games_fetch_returns = [
            NormalizedGame(
                season="2025-26",
                game_date="2025-10-22",
                season_type="regular",
                away_team="Boston Celtics",
                home_team="New York Knicks",
                matchup="BOS@NYK",
            ),
        ]

        with patch(
            f"{_API_MODULE}.fetch_season_schedule",
            return_value=games_fetch_returns,
        ), patch(
            f"{_API_MODULE}.detect_current_season",
            return_value="2025-26",
        ), patch(
            "app.jobs.sync_nba_schedule.build_engine",
            return_value=engine,
        ):
            main(["sync-live", "--season", "2025-26"])

        with Session(engine, expire_on_commit=False) as session:
            count = session.scalar(select(func.count()).select_from(NBAScheduleGame))
            assert count == 1
            game = session.scalar(select(NBAScheduleGame))
            assert game.matchup == "BOS@NYK"
            assert game.season_type == "regular"

    def test_fetch_dedup_called_with_all_types(self, engine):
        """When default season types are used, fetch_season_schedule receives all four."""
        captured_types = []

        def mock_fetch(season, types, **kwargs):
            captured_types.extend(types)
            return []

        with patch(
            f"{_API_MODULE}.fetch_season_schedule",
            side_effect=mock_fetch,
        ), patch(
            f"{_API_MODULE}.detect_current_season",
            return_value="2025-26",
        ), patch(
            "app.jobs.sync_nba_schedule.build_engine",
            return_value=engine,
        ):
            main(["sync-live", "--season", "2025-26"])

        assert captured_types == list(SEASON_TYPES_API)


# ---------------------------------------------------------------------------
# Dry-run performs no DB writes
# ---------------------------------------------------------------------------


class TestDryRun:
    """Dry-run mode must not write to the database."""

    def test_dry_run_with_all_types(self, engine):
        """Default sync with --dry-run fetches all types but writes nothing."""
        captured_types = []

        def mock_fetch(season, types, **kwargs):
            captured_types.extend(types)
            return SAMPLE_API_GAMES

        with patch(
            f"{_API_MODULE}.fetch_season_schedule",
            side_effect=mock_fetch,
        ), patch(
            f"{_API_MODULE}.detect_current_season",
            return_value="2025-26",
        ), patch(
            "app.jobs.sync_nba_schedule.build_engine",
            return_value=engine,
        ):
            result = main(["sync-live", "--season", "2025-26", "--dry-run"])

        assert result == 0
        assert captured_types == list(SEASON_TYPES_API)

        with Session(engine, expire_on_commit=False) as session:
            count = session.scalar(select(func.count()).select_from(NBAScheduleGame))
            assert count == 0

    def test_dry_run_with_explicit_types(self, engine):
        """Explicit --season-type with --dry-run fetches only those types, writes nothing."""
        captured_types = []

        def mock_fetch(season, types, **kwargs):
            captured_types.extend(types)
            return SAMPLE_API_GAMES

        with patch(
            f"{_API_MODULE}.fetch_season_schedule",
            side_effect=mock_fetch,
        ), patch(
            f"{_API_MODULE}.detect_current_season",
            return_value="2025-26",
        ), patch(
            "app.jobs.sync_nba_schedule.build_engine",
            return_value=engine,
        ):
            result = main([
                "sync-live", "--season", "2025-26", "--dry-run",
                "--season-type", "Playoffs",
            ])

        assert result == 0
        assert captured_types == ["Playoffs"]

        with Session(engine, expire_on_commit=False) as session:
            count = session.scalar(select(func.count()).select_from(NBAScheduleGame))
            assert count == 0


# ---------------------------------------------------------------------------
# Regression: existing modes still work
# ---------------------------------------------------------------------------


class TestExistingModesStillWork:
    def test_import_csv_subcommand_exists(self):
        parser = build_parser()
        args = parser.parse_args(["import-csv", "some/path.csv"])
        assert args.command == "import-csv"
        assert args.path == "some/path.csv"

    def test_upsert_subcommand_exists(self):
        parser = build_parser()
        args = parser.parse_args(["upsert", "--season", "2025-26"])
        assert args.command == "upsert"
        assert args.season == "2025-26"
