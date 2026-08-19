from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.jobs.update_nba_daily import run_daily_update


@pytest.fixture
def mock_deps():
    with patch("app.jobs.update_nba_daily.build_engine") as mock_engine, \
         patch("app.jobs.update_nba_daily.build_session_factory") as mock_session_factory, \
         patch("app.jobs.update_nba_daily.backfill_main") as mock_backfill:

        mock_session = MagicMock()
        mock_cm = mock_session_factory.return_value.return_value
        mock_cm.__enter__.return_value = mock_session
        mock_cm.__exit__.return_value = False

        yield {
            "engine": mock_engine,
            "session_factory": mock_session_factory,
            "session": mock_session,
            "backfill": mock_backfill,
        }


class TestRunDailyUpdate:
    def test_creates_update_run_and_marks_completed(self, mock_deps):
        run_daily_update(date(2025, 11, 15), date(2025, 11, 15))

        session = mock_deps["session"]
        session.add.assert_called_once()
        run_obj = session.add.call_args[0][0]
        assert run_obj.requested_start_date == date(2025, 11, 15)
        assert run_obj.requested_end_date == date(2025, 11, 15)
        assert run_obj.status == "completed"
        assert run_obj.finished_at is not None
        assert mock_deps["backfill"].call_count == 1

    def test_default_uses_direct_nba_flag(self, mock_deps):
        import sys

        captured_argv = []
        mock_deps["backfill"].side_effect = lambda: captured_argv.extend(sys.argv)

        run_daily_update(date(2025, 11, 15), date(2025, 11, 15))

        assert "--direct-nba" in captured_argv

    def test_registered_only_skips_direct_nba(self, mock_deps):
        import sys

        captured_argv = []
        mock_deps["backfill"].side_effect = lambda: captured_argv.extend(sys.argv)

        run_daily_update(
            date(2025, 11, 15), date(2025, 11, 15), registered_only=True
        )

        assert "--registered-only" in captured_argv
        assert "--direct-nba" not in captured_argv

    def test_marks_failed_on_exception(self, mock_deps):
        mock_deps["backfill"].side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            run_daily_update(date(2025, 11, 15), date(2025, 11, 15))

        session = mock_deps["session"]
        run_obj = session.add.call_args[0][0]
        assert run_obj.status == "failed"
        assert run_obj.error_details == "boom"

    def test_restores_sys_argv_on_success(self, mock_deps):
        import sys

        original_argv = sys.argv[:]
        run_daily_update(date(2025, 11, 15), date(2025, 11, 15))
        assert sys.argv == original_argv

    def test_restores_sys_argv_on_failure(self, mock_deps):
        import sys

        mock_deps["backfill"].side_effect = RuntimeError("fail")
        original_argv = sys.argv[:]

        with pytest.raises(RuntimeError):
            run_daily_update(date(2025, 11, 15), date(2025, 11, 15))

        assert sys.argv == original_argv

    def test_finished_at_is_aware_utc_and_not_before_started_at(self, mock_deps):
        before = datetime.now(tz=timezone.utc)
        run_daily_update(date(2025, 11, 15), date(2025, 11, 15))

        run_obj = mock_deps["session"].add.call_args[0][0]
        finished = run_obj.finished_at
        assert finished is not None
        assert finished.tzinfo is not None, "finished_at must be timezone-aware"
        assert finished >= before, "finished_at must not precede the call"

        # Simulate the DB-populated started_at (server_default=func.now())
        # and verify the invariant holds regardless of timezone convention.
        started_simulated = before
        assert finished >= started_simulated