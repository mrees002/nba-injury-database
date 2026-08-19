"""Focused offline tests for the lean production data transfer utility.

No database connections required.  Tests cover URL handling, SQL generation,
table ordering, and dry-run behaviour using mocked psycopg connections.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

from scripts.transfer_to_production import (
    TRANSFER_TABLES,
    _build_upsert,
    _sync_sequences,
    _transfer_table,
    main,
    mask_url,
    normalize_psycopg_url,
)

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

class TestNormalizePsycopgUrl:
    def test_strips_psycopg_dialect(self):
        url = "postgresql+psycopg://user:pass@host:5432/db"
        assert normalize_psycopg_url(url) == "postgresql://user:pass@host:5432/db"

    def test_plain_postgresql_unchanged(self):
        url = "postgresql://user:pass@host:5432/db"
        assert normalize_psycopg_url(url) == url

    def test_asyncpg_not_affected(self):
        url = "postgresql+asyncpg://user:pass@host:5432/db"
        assert normalize_psycopg_url(url) == url

    def test_empty_string(self):
        assert normalize_psycopg_url("") == ""


class TestMaskUrl:
    def test_password_is_redacted(self):
        url = "postgresql://nba:secret123@localhost:5432/nba_injuries"
        masked = mask_url(url)
        assert "secret123" not in masked
        assert "***" in masked
        assert "localhost" in masked

    def test_no_password_unchanged(self):
        url = "postgresql://localhost:5432/nba_injuries"
        assert mask_url(url) == url

    def test_malformed_url_returns_input(self):
        assert mask_url("not-a-url") == "not-a-url"


# ---------------------------------------------------------------------------
# Table order
# ---------------------------------------------------------------------------

class TestTransferTableOrder:
    def test_four_tables(self):
        assert len(TRANSFER_TABLES) == 4

    def test_referenced_tables_first(self):
        """nba_players and nba_teams must come before public_injury_entries."""
        players_idx = TRANSFER_TABLES.index("nba_players")
        teams_idx = TRANSFER_TABLES.index("nba_teams")
        entries_idx = TRANSFER_TABLES.index("public_injury_entries")
        assert players_idx < entries_idx
        assert teams_idx < entries_idx

    def test_expected_tables(self):
        assert TRANSFER_TABLES == [
            "nba_players",
            "nba_teams",
            "nba_schedule_games",
            "public_injury_entries",
        ]


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------

class TestBuildUpsert:
    def test_simple_table(self):
        sql = _build_upsert("nba_players", ["id", "canonical_name", "name_key"])
        assert "INSERT INTO nba_players" in sql
        assert "ON CONFLICT (id) DO UPDATE SET" in sql
        assert "canonical_name = EXCLUDED.canonical_name" in sql
        assert "name_key = EXCLUDED.name_key" in sql
        # id must NOT appear in the UPDATE SET
        assert "id = EXCLUDED.id" not in sql

    def test_all_columns_except_id_updated(self):
        cols = ["id", "col_a", "col_b", "col_c"]
        sql = _build_upsert("test_table", cols)
        for c in cols:
            if c != "id":
                assert f"{c} = EXCLUDED.{c}" in sql

    def test_placeholder_count_matches_columns(self):
        cols = ["id", "a", "b"]
        sql = _build_upsert("t", cols)
        assert sql.count("%s") == 3


# ---------------------------------------------------------------------------
# Transfer with mocked connections
# ---------------------------------------------------------------------------

def _make_cursor(columns, rows):
    """Build a mock psycopg.Cursor that returns given columns/rows."""
    cur = MagicMock()
    # LIMIT 0 query -> column descriptions
    desc = [SimpleNamespace(name=c) for c in columns]
    cur.description = desc
    cur.fetchall.return_value = rows
    cur.fetchone.return_value = [len(rows)]
    return cur


class TestTransferTable:
    def test_empty_source_returns_zero_counts(self):
        src = MagicMock()
        src_cur = MagicMock()
        src_cur.description = []
        src_cur.fetchall.return_value = []
        src.cursor.return_value.__enter__ = MagicMock(return_value=src_cur)
        src.cursor.return_value.__exit__ = MagicMock(return_value=False)

        dst_cur = MagicMock()
        dst_cur.connection = MagicMock()
        # count query returns 0
        dst_cur.fetchone.return_value = [0]

        result = _transfer_table(src, dst_cur, "nba_players", dry_run=False)
        assert result["source"] == 0
        assert result["inserted"] == 0

    def test_dry_run_does_not_write(self):
        src = MagicMock()
        src_cur = MagicMock()
        src_cur.description = [SimpleNamespace(name="id")]
        src_cur.fetchall.return_value = [(1,), (2,)]
        src.cursor.return_value.__enter__ = MagicMock(return_value=src_cur)
        src.cursor.return_value.__exit__ = MagicMock(return_value=False)

        dst_cur = MagicMock()
        dst_cur.connection = MagicMock()
        dst_cur.fetchone.return_value = [0]

        result = _transfer_table(src, dst_cur, "nba_players", dry_run=True)
        assert result["source"] == 2
        assert result["inserted"] == 0
        dst_cur.executemany.assert_not_called()

    def test_rows_are_batched(self):
        rows = [(i,) for i in range(2500)]
        src = MagicMock()
        src_cur = MagicMock()
        src_cur.description = [SimpleNamespace(name="id")]
        src_cur.fetchall.return_value = rows
        src.cursor.return_value.__enter__ = MagicMock(return_value=src_cur)
        src.cursor.return_value.__exit__ = MagicMock(return_value=False)

        dst_cur = MagicMock()
        dst_cur.connection = MagicMock()
        dst_cur.fetchone.return_value = [0]

        result = _transfer_table(src, dst_cur, "nba_players", dry_run=False)
        assert result["inserted"] == 2500
        # 2500 rows / 1000 batch = 3 executemany calls
        assert dst_cur.executemany.call_count == 3


# ---------------------------------------------------------------------------
# Sequence sync
# ---------------------------------------------------------------------------

class TestSyncSequences:
    def test_sets_sequence_to_max_id(self):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        # Simulate: MAX(id)=100 for all tables, sequence exists
        cur.fetchone.side_effect = [
            [100],  # nba_players MAX(id)
            ["nba_players_id_seq"],  # pg_get_serial_sequence
            [100],  # nba_teams MAX(id)
            ["nba_teams_id_seq"],
            [500],  # nba_schedule_games MAX(id)
            ["nba_schedule_games_id_seq"],
            [1000],  # public_injury_entries MAX(id)
            ["public_injury_entries_id_seq"],
        ]

        _sync_sequences(conn)

        # Should have called setval for each table with a sequence
        setval_calls = [
            c for c in cur.execute.call_args_list
            if c[0][0].startswith("SELECT setval")
        ]
        assert len(setval_calls) == 4

    def test_skips_tables_with_no_rows(self):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        # All tables empty
        cur.fetchone.return_value = [0]

        _sync_sequences(conn)

        # No setval calls
        setval_calls = [
            c for c in cur.execute.call_args_list
            if c[0][0].startswith("SELECT setval")
        ]
        assert len(setval_calls) == 0

    def test_skips_tables_with_no_sequence(self):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        # MAX(id)=50 but no sequence
        cur.fetchone.side_effect = [
            [50],   # nba_players MAX(id)
            [None],  # pg_get_serial_sequence returns None
            [0],     # nba_teams empty
            [0],     # nba_schedule_games empty
            [0],     # public_injury_entries empty
        ]

        _sync_sequences(conn)

        setval_calls = [
            c for c in cur.execute.call_args_list
            if c[0][0].startswith("SELECT setval")
        ]
        assert len(setval_calls) == 0


# ---------------------------------------------------------------------------
# Regression: source connection lifecycle
# ---------------------------------------------------------------------------

class TestConnectionLifecycle:
    """Regression: the source connection must stay open through every
    _transfer_table call.  Earlier code closed it inside a ``with`` block
    right after the row-count phase, causing ``OperationalError: the
    connection is closed`` when the real transfer began."""

    @patch("scripts.transfer_to_production._source_counts")
    @patch("scripts.transfer_to_production._transfer_table")
    @patch("scripts.transfer_to_production._sync_sequences")
    @patch("scripts.transfer_to_production._print_report")
    @patch("scripts.transfer_to_production.psycopg")
    def test_source_conn_still_open_when_transfer_begins(
        self, mock_psycopg, _print_rpt, _sync_fn, _xfer_fn, _counts_fn,
    ):
        """After source row-counts, the source connection must not be closed
        before _transfer_table is called for the first table."""
        src = MagicMock()
        dst = MagicMock()
        mock_psycopg.connect.side_effect = [src, dst]

        # Set up dst cursor context manager for the information_schema check
        info_cur = MagicMock()
        info_cur.fetchall.return_value = [(t,) for t in TRANSFER_TABLES]
        dst.cursor.return_value.__enter__ = MagicMock(return_value=info_cur)
        dst.cursor.return_value.__exit__ = MagicMock(return_value=False)

        _counts_fn.return_value = {t: 10 for t in TRANSFER_TABLES}

        # Track whether src.close() was called before each _transfer_table call
        src_was_closed_before_transfer = []

        def _tracking_transfer(src_conn, dst_cur, table, *, dry_run=False):
            src_was_closed_before_transfer.append(src.close.called)
            return {"source": 10, "inserted": 10, "updated": 0, "dest": 10}

        _xfer_fn.side_effect = _tracking_transfer

        with patch.object(sys, "argv", [
            "transfer_to_production.py",
            "--target-database-url", "postgresql://t@localhost/t",
            "--source-database-url", "postgresql://s@localhost/s",
        ]):
            main()

        assert _xfer_fn.call_count == len(TRANSFER_TABLES)
        assert len(src_was_closed_before_transfer) == len(TRANSFER_TABLES)
        # The critical invariant: src.close() must NOT have been called
        # before any _transfer_table invocation.
        assert not any(src_was_closed_before_transfer), (
            "src.close() was called before _transfer_table"
        )
        # src.close() IS called at the end (finally block)
        src.close.assert_called_once()
