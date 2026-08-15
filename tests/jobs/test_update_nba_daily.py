from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from app.jobs.update_nba_daily import main


@pytest.fixture
def mock_deps():
    with patch("app.jobs.update_nba_daily.build_engine") as mock_engine, \
         patch("app.jobs.update_nba_daily.build_session_factory") as mock_session_factory, \
         patch("app.jobs.update_nba_daily.backfill_main") as mock_backfill, \
         patch("app.jobs.update_nba_daily.reclassify_main") as mock_reclassify, \
         patch("app.jobs.update_nba_daily.rebuild_main") as mock_rebuild, \
         patch("sys.argv", ["update_nba_daily", "--start-date", "2024-01-01", "--end-date", "2024-01-31"]):

        mock_session = MagicMock()
        mock_cm = mock_session_factory.return_value.return_value
        mock_cm.__enter__.return_value = mock_session
        mock_cm.__exit__.return_value = False

        yield {
            "engine": mock_engine,
            "session_factory": mock_session_factory,
            "session": mock_session,
            "backfill": mock_backfill,
            "reclassify": mock_reclassify,
            "rebuild": mock_rebuild,
        }


def test_pipeline_runs_in_order_and_marks_completed(mock_deps):
    main()
    
    mock_deps["backfill"].assert_called_once()
    mock_deps["reclassify"].assert_called_once()
    mock_deps["rebuild"].assert_called_once()
    
    expected_order = [
        call(),
        call(),
        call(),
    ]
    actual_calls = [
        mock_deps["backfill"].call_args,
        mock_deps["reclassify"].call_args,
        mock_deps["rebuild"].call_args,
    ]
    assert actual_calls == expected_order
    
    assert mock_deps["session"].commit.call_count >= 2
    
    run_obj = mock_deps["session"].add.call_args[0][0]
    assert run_obj.status == "completed"
    assert run_obj.finished_at is not None