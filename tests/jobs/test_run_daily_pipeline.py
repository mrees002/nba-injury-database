from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.jobs.run_daily_pipeline import main, _run_schedule_sync, _run_daily_updater


# ---------------------------------------------------------------------------
# _run_schedule_sync
# ---------------------------------------------------------------------------

class TestRunScheduleSync:
    @patch("app.jobs.run_daily_pipeline.upsert_schedule_rows")
    @patch("app.jobs.run_daily_pipeline.fetch_season_schedule")
    @patch("app.jobs.run_daily_pipeline.detect_current_season")
    @patch("app.jobs.run_daily_pipeline.build_session_factory")
    @patch("app.jobs.run_daily_pipeline.build_engine")
    def test_calls_api_and_upserts(
        self, mock_engine, mock_sf, mock_detect, mock_fetch, mock_upsert
    ):
        mock_detect.return_value = "2025-26"
        mock_fetch.return_value = [MagicMock()]
        mock_upsert.return_value = MagicMock(upserted=1, skipped=0)

        _run_schedule_sync()

        mock_detect.assert_called_once()
        mock_fetch.assert_called_once_with("2025-26", [
            "Pre Season", "Regular Season", "PlayIn", "Playoffs"
        ])
        mock_upsert.assert_called_once()
        call_args = mock_upsert.call_args
        assert call_args[1]["source"] == "nba_stats_api"
        mock_engine.return_value.dispose.assert_called_once()

    @patch("app.jobs.run_daily_pipeline.fetch_season_schedule", side_effect=RuntimeError("net"))
    @patch("app.jobs.run_daily_pipeline.detect_current_season", return_value="2025-26")
    def test_propagates_schedule_fetch_error(self, _detect, _fetch):
        with pytest.raises(RuntimeError, match="net"):
            _run_schedule_sync()


# ---------------------------------------------------------------------------
# _run_daily_updater
# ---------------------------------------------------------------------------

class TestRunDailyUpdater:
    @patch("app.jobs.run_daily_pipeline.run_daily_update")
    def test_calls_run_daily_update(self, mock_update):
        target = date(2025, 11, 15)

        _run_daily_updater(target)

        mock_update.assert_called_once_with(target, target)

    @patch("app.jobs.run_daily_pipeline.run_daily_update", side_effect=RuntimeError("boom"))
    def test_propagates_update_error(self, _update):
        with pytest.raises(RuntimeError, match="boom"):
            _run_daily_updater(date(2025, 11, 15))


# ---------------------------------------------------------------------------
# main (full pipeline)
# ---------------------------------------------------------------------------

class TestMainPipeline:
    @patch("app.jobs.run_daily_pipeline._run_daily_updater")
    @patch("app.jobs.run_daily_pipeline._run_schedule_sync")
    def test_success_returns_zero(self, mock_sync, mock_updater):
        result = main()
        assert result == 0
        mock_sync.assert_called_once()
        mock_updater.assert_called_once()

    @patch("app.jobs.run_daily_pipeline._run_daily_updater")
    @patch("app.jobs.run_daily_pipeline._run_schedule_sync", side_effect=RuntimeError("fail"))
    def test_schedule_sync_failure_returns_nonzero(self, mock_sync, mock_updater):
        result = main()
        assert result != 0
        mock_updater.assert_not_called()

    @patch("app.jobs.run_daily_pipeline._run_daily_updater", side_effect=RuntimeError("fail"))
    @patch("app.jobs.run_daily_pipeline._run_schedule_sync")
    def test_updater_failure_returns_nonzero(self, mock_sync, mock_updater):
        result = main()
        assert result != 0

    @patch("app.jobs.run_daily_pipeline._run_daily_updater", side_effect=KeyboardInterrupt())
    @patch("app.jobs.run_daily_pipeline._run_schedule_sync")
    def test_keyboard_interrupt_propagates(self, _sync, _updater):
        with pytest.raises(KeyboardInterrupt):
            main()
