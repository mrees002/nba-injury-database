"""Real PostgreSQL regression tests; TEST_POSTGRES_URL must allow CREATE DATABASE."""

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[1]
TABLES = ("nba_players", "nba_teams", "nba_schedule_games", "update_runs", "public_injury_entries")
REPAIR = (ROOT / "scripts/repair_production_ids.sql").read_text()


@pytest.fixture
def postgres():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL to run PostgreSQL ID repair tests")
    name = f"test_id_repair_{uuid4().hex}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        options = conninfo_to_dict(url)
        options["dbname"] = name
        try:
            with psycopg.connect(make_conninfo(**options), autocommit=True) as connection:
                yield connection
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def test_repair_preserves_imported_rows_and_is_repeatable(postgres):
    for table in TABLES:
        postgres.execute(
            sql.SQL("CREATE TABLE {} (id BIGINT PRIMARY KEY, payload TEXT)").format(
                sql.Identifier(table)
            )
        )
        postgres.execute(
            sql.SQL("INSERT INTO {} VALUES (500, 'existing')").format(sql.Identifier(table))
        )
    for _ in range(2):
        postgres.execute(REPAIR)
    for table in TABLES:
        result = postgres.execute(
            sql.SQL("INSERT INTO {} (payload) VALUES ('new') RETURNING id").format(
                sql.Identifier(table)
            )
        ).fetchone()
        assert result[0] > 500
        assert postgres.execute(
            sql.SQL("SELECT payload FROM {} WHERE id = 500").format(sql.Identifier(table))
        ).fetchone() == ("existing",)


def test_repair_handles_existing_sequences_and_empty_tables(postgres):
    for table in TABLES:
        postgres.execute(
            sql.SQL("CREATE TABLE {} (id BIGSERIAL PRIMARY KEY)").format(sql.Identifier(table))
        )
    postgres.execute("SELECT setval(pg_get_serial_sequence('nba_players', 'id'), 900, true)")
    postgres.execute("INSERT INTO nba_teams VALUES (500)")
    postgres.execute(REPAIR)
    assert (
        postgres.execute("INSERT INTO nba_players DEFAULT VALUES RETURNING id").fetchone()[0] > 900
    )
    assert postgres.execute("INSERT INTO nba_teams DEFAULT VALUES RETURNING id").fetchone()[0] > 500
    assert postgres.execute("INSERT INTO update_runs DEFAULT VALUES RETURNING id").fetchone()[0] > 0


def test_fresh_bootstrap_generates_ids_and_supports_repair(postgres):
    postgres.execute((ROOT / "scripts/lean_production_bootstrap.sql").read_text())
    postgres.execute(
        "INSERT INTO nba_players (id, canonical_name, name_key) VALUES (500, 'Existing', 'existing')"
    )
    postgres.execute(REPAIR)
    for _ in range(2):
        player = postgres.execute(
            "INSERT INTO nba_players (canonical_name, name_key) VALUES (%s, %s) RETURNING id",
            (uuid4().hex, uuid4().hex),
        ).fetchone()[0]
        assert player > 500
    for table in TABLES:
        assert postgres.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table,)).fetchone()[0]
