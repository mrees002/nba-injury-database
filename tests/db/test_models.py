from sqlalchemy import CheckConstraint, UniqueConstraint

from app.db.base import Base
from app.models import Injury, RawTransaction, UpdateRun


def test_required_tables_and_columns_are_registered():
    assert set(Base.metadata.tables) == {"raw_transactions", "injuries", "update_runs"}
    assert set(RawTransaction.__table__.columns.keys()) == {
        "id",
        "source_type",
        "transaction_date",
        "team",
        "acquired",
        "relinquished",
        "notes",
        "source_url",
        "source_row_key",
        "scraped_at",
        "created_at",
    }
    assert set(Injury.__table__.columns.keys()) == {
        "id",
        "date",
        "season",
        "player_name",
        "team",
        "body_part",
        "injury_type",
        "notes",
        "preferred_source",
        "source_raw_transaction_id",
        "created_at",
        "updated_at",
    }
    assert set(UpdateRun.__table__.columns.keys()) == {
        "id",
        "started_at",
        "finished_at",
        "requested_start_date",
        "requested_end_date",
        "rows_fetched",
        "rows_inserted",
        "rows_processed",
        "status",
        "error_details",
    }


def test_raw_transaction_enforces_source_type_and_idempotency_constraints():
    constraints = RawTransaction.__table__.constraints

    assert any(
        isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_raw_transactions_source_type_allowed"
        for constraint in constraints
    )
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_raw_transactions_source_type_source_row_key"
        and [column.name for column in constraint.columns] == ["source_type", "source_row_key"]
        for constraint in constraints
    )


def test_schema_indexes_match_the_proposed_schema():
    assert {index.name for index in RawTransaction.__table__.indexes} == {
        "ix_raw_transactions_date",
        "ix_raw_transactions_relinquished",
    }
    assert {index.name for index in Injury.__table__.indexes} == {
        "ix_injuries_date",
        "ix_injuries_player_name",
        "ix_injuries_team",
        "ix_injuries_season",
        "ix_injuries_body_part",
        "ix_injuries_injury_type",
    }


def test_injury_preserves_raw_transaction_lineage():
    foreign_keys = Injury.__table__.c.source_raw_transaction_id.foreign_keys

    assert len(foreign_keys) == 1
    assert next(iter(foreign_keys)).target_fullname == "raw_transactions.id"
