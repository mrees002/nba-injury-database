"""Offline verification that the lean-production SQL bootstrap matches models.

No database connection is required.  The test reads
``scripts/lean_production_bootstrap.sql`` and checks that it contains exactly
the tables, constraints, and indexes expected by the SQLAlchemy models
used by the public API and the incremental update job.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SQL_BOOTSTRAP = PROJECT_ROOT / "scripts" / "lean_production_bootstrap.sql"

# Tables that must appear in the bootstrap
EXPECTED_TABLES = {
    "nba_players",
    "nba_teams",
    "nba_schedule_games",
    "update_runs",
    "public_injury_entries",
    "alembic_version",
}

# Tables that must NOT appear in the bootstrap
FORBIDDEN_TABLES = {
    "raw_transactions",
    "injuries",
    "nba_report_candidates",
    "nba_reports",
    "nba_report_entries",
    "nba_injury_conditions",
    "nba_games",
    "nba_injury_episodes",
    "nba_injury_episode_conditions",
}

# Expected primary key constraint names
EXPECTED_PK = {
    "pk_nba_players",
    "pk_nba_teams",
    "pk_nba_schedule_games",
    "pk_update_runs",
    "pk_public_injury_entries",
    "alembic_version_pkc",
}

# Expected unique constraint names
EXPECTED_UQ = {
    "uq_nba_players_name_key",
    "uq_nba_players_official_id",
    "uq_nba_teams_canonical_name",
    "uq_nba_teams_abbreviation",
    "uq_nba_schedule_season_date_matchup",
    "uq_public_injury_entries_url_row",
}

# Expected index names
EXPECTED_INDEXES = {
    "ix_nba_schedule_season",
    "ix_nba_schedule_game_date",
    "ix_public_injury_entries_game_date",
    "ix_public_injury_entries_player_id",
    "ix_public_injury_entries_team_id",
    "ix_public_injury_entries_season",
    "ix_public_injury_entries_season_type",
}

# Expected FK constraint names
EXPECTED_FKS = {
    "fk_public_injury_entries_player_id_nba_players",
    "fk_public_injury_entries_team_id_nba_teams",
}


@pytest.fixture(scope="module")
def sql_text() -> str:
    return SQL_BOOTSTRAP.read_text()


def _create_table_names(sql: str) -> set[str]:
    return set(re.findall(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)", sql))


def _constraint_names(sql: str, pattern: str) -> set[str]:
    # Flatten to single-line so multi-line CONSTRAINT declarations match
    flat = " ".join(sql.split())
    return set(re.findall(pattern, flat, re.IGNORECASE))


def _index_names(sql: str) -> set[str]:
    flat = " ".join(sql.split())
    return set(re.findall(r"CREATE INDEX (?:IF NOT EXISTS )?(\w+)", flat))


# ── Table presence ────────────────────────────────────────────────────────

def test_bootstrap_file_exists():
    assert SQL_BOOTSTRAP.is_file(), f"Missing {SQL_BOOTSTRAP}"


def test_contains_all_expected_tables(sql_text):
    tables = _create_table_names(sql_text)
    missing = EXPECTED_TABLES - tables
    assert not missing, f"Missing tables in bootstrap: {missing}"


def test_does_not_contain_forbidden_tables(sql_text):
    tables = _create_table_names(sql_text)
    present = FORBIDDEN_TABLES & tables
    assert not present, f"Forbidden tables found in bootstrap: {present}"


# ── Constraints ───────────────────────────────────────────────────────────

def test_primary_key_constraints(sql_text):
    pks = _constraint_names(sql_text, r"CONSTRAINT (\w+) PRIMARY KEY")
    missing = EXPECTED_PK - pks
    assert not missing, f"Missing PK constraints: {missing}"


def test_unique_constraints(sql_text):
    uqs = _constraint_names(sql_text, r"CONSTRAINT (\w+) UNIQUE")
    missing = EXPECTED_UQ - uqs
    assert not missing, f"Missing UNIQUE constraints: {missing}"


# ── Indexes ───────────────────────────────────────────────────────────────

def test_indexes(sql_text):
    indexes = _index_names(sql_text)
    missing = EXPECTED_INDEXES - indexes
    assert not missing, f"Missing indexes: {missing}"


# ── Foreign keys ──────────────────────────────────────────────────────────

def test_foreign_key_references_nba_players(sql_text):
    assert "REFERENCES nba_players(id)" in sql_text


def test_foreign_key_references_nba_teams(sql_text):
    assert "REFERENCES nba_teams(id)" in sql_text


# ── Alembic version stamp ─────────────────────────────────────────────────

def test_alembic_version_stamped(sql_text):
    assert "0007_public_injury_entries" in sql_text


def test_alembic_version_insert_uses_on_conflict(sql_text):
    """The INSERT must be idempotent (ON CONFLICT DO NOTHING)."""
    assert "ON CONFLICT DO NOTHING" in sql_text


# ── Wrapped in transaction ────────────────────────────────────────────────

def test_bootstrap_uses_transaction(sql_text):
    # Strip leading SQL comments to find the first statement
    stripped = re.sub(r"^\s*--.*$", "", sql_text, flags=re.MULTILINE).strip()
    assert stripped.startswith("BEGIN"), "SQL bootstrap must start with BEGIN"
    assert stripped.endswith("COMMIT;"), "SQL bootstrap must end with COMMIT;"


# ── Cross-check: model table count vs bootstrap ──────────────────────────

def test_model_tables_count():
    """Verify the number of production model tables matches bootstrap.

    This is a lightweight cross-check: import the models and count
    the tables that should exist in production.
    """
    from app.models.nba import NBAPlayer, NBAScheduleGame, NBATeam, PublicInjuryEntry
    from app.models.update_run import UpdateRun

    production_models = [NBAPlayer, NBATeam, NBAScheduleGame, PublicInjuryEntry, UpdateRun]
    model_tables = {m.__tablename__ for m in production_models}
    assert model_tables == EXPECTED_TABLES - {"alembic_version"}
