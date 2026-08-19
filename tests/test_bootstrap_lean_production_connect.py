"""Focused tests for _connect URL normalization in bootstrap_lean_production."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scripts.bootstrap_lean_production import _connect


@pytest.fixture(autouse=True)
def _patch_psycopg(monkeypatch):
    """Prevent real DB connections; mock the first-available driver."""
    mock_psycopg = MagicMock()
    mock_psycopg.connect.return_value = MagicMock()
    monkeypatch.setitem(
        __import__("sys").modules, "psycopg", mock_psycopg
    )
    return mock_psycopg


class TestConnectUrlNormalization:
    def test_psycopg_plus_url_is_normalized(self, _patch_psycopg):
        url = "postgresql+psycopg://user:pass@host:5432/db"
        _connect(url)
        _patch_psycopg.connect.assert_called_once_with(
            "postgresql://user:pass@host:5432/db"
        )

    def test_plain_postgresql_url_unchanged(self, _patch_psycopg):
        url = "postgresql://user:pass@host:5432/db"
        _connect(url)
        _patch_psycopg.connect.assert_called_once_with(url)

    def test_other_drivers_not_normalized(self, _patch_psycopg):
        url = "postgresql+asyncpg://user:pass@host:5432/db"
        _connect(url)
        # asyncpg scheme is unrelated to psycopg; must be passed through
        _patch_psycopg.connect.assert_called_once_with(url)
