from __future__ import annotations

import csv
from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.jobs import import_csv as import_csv_job
from app.models import RawTransaction
from app.services.csv_import import CSVValidationError, import_historical_csv

HEADERS = ["Date", "Team", "Acquired", "Relinquished", "Notes"]


@pytest.fixture
def engine(tmp_path):
    database_engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(database_engine)
    try:
        yield database_engine
    finally:
        database_engine.dispose()


def write_csv(path, rows, headers=HEADERS):
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(headers)
        writer.writerows(rows)


def test_import_preserves_text_normalizes_dates_and_is_idempotent(tmp_path, engine):
    csv_path = tmp_path / "transactions.csv"
    write_csv(
        csv_path,
        [
            ["01/02/2024", " BOS ", "", "Player One", "  sore left knee  "],
            ["2024-01-03", "LAL", "", "Player Two", "sprained ankle"],
        ],
    )

    with Session(engine, expire_on_commit=False) as session:
        first_result = import_historical_csv(session, "il", csv_path)
        records = list(
            session.scalars(select(RawTransaction).order_by(RawTransaction.transaction_date))
        )
        second_result = import_historical_csv(session, "il", csv_path)
        database_count = session.scalar(select(func.count()).select_from(RawTransaction))

    assert first_result.read == 2
    assert first_result.inserted == 2
    assert first_result.skipped == 0
    assert first_result.invalid == 0
    assert records[0].transaction_date == date(2024, 1, 2)
    assert records[0].team == " BOS "
    assert records[0].acquired == ""
    assert records[0].notes == "  sore left knee  "
    assert records[0].source_url is None
    assert len(records[0].source_row_key) == 64
    assert second_result.read == 2
    assert second_result.inserted == 0
    assert second_result.skipped == 2
    assert second_result.invalid == 0
    assert database_count == 2


def test_equivalent_date_formats_generate_the_same_row_key(tmp_path, engine):
    csv_path = tmp_path / "duplicate_dates.csv"
    write_csv(
        csv_path,
        [
            ["01/02/2024", "BOS", "", "Player One", "sore knee"],
            ["2024-01-02", "BOS", "", "Player One", "sore knee"],
        ],
    )

    with Session(engine, expire_on_commit=False) as session:
        result = import_historical_csv(session, "missed_game", csv_path)

    assert result.read == 2
    assert result.inserted == 1
    assert result.skipped == 1
    assert result.invalid == 0


def test_overlapping_rows_in_differently_named_files_insert_only_once(tmp_path, engine):
    first_path = tmp_path / "base_file.csv"
    overlap_path = tmp_path / "later_overlap_file.csv"
    overlapping_row = ["2024-01-02", "BOS", "", "Player One", "sore knee"]
    write_csv(first_path, [overlapping_row])
    write_csv(
        overlap_path,
        [
            overlapping_row,
            ["2024-01-03", "LAL", "", "Player Two", "sprained ankle"],
        ],
    )

    with Session(engine, expire_on_commit=False) as session:
        first_result = import_historical_csv(session, "il", first_path)
        overlap_result = import_historical_csv(session, "il", overlap_path)
        database_count = session.scalar(select(func.count()).select_from(RawTransaction))

    assert first_result.inserted == 1
    assert overlap_result.read == 2
    assert overlap_result.inserted == 1
    assert overlap_result.skipped == 1
    assert overlap_result.invalid == 0
    assert database_count == 2


def test_invalid_dates_and_malformed_rows_are_counted_without_insertion(tmp_path, engine):
    csv_path = tmp_path / "invalid.csv"
    write_csv(
        csv_path,
        [
            ["13/40/2024", "BOS", "", "Player One", "sore knee"],
            ["2024-01-02", "BOS", "", "Player Two"],
        ],
    )

    with Session(engine, expire_on_commit=False) as session:
        result = import_historical_csv(session, "il", csv_path)
        database_count = session.scalar(select(func.count()).select_from(RawTransaction))

    assert result.read == 2
    assert result.inserted == 0
    assert result.skipped == 0
    assert result.invalid == 2
    assert database_count == 0


def test_missing_required_columns_fail_before_database_changes(tmp_path, engine):
    csv_path = tmp_path / "missing_column.csv"
    write_csv(
        csv_path,
        [["2024-01-02", "BOS", "", "Player One"]],
        headers=["Date", "Team", "Acquired", "Relinquished"],
    )

    with Session(engine, expire_on_commit=False) as session:
        with pytest.raises(CSVValidationError, match="Notes"):
            import_historical_csv(session, "il", csv_path)
        database_count = session.scalar(select(func.count()).select_from(RawTransaction))

    assert database_count == 0


def test_source_type_must_be_explicit_and_supported(tmp_path, engine):
    csv_path = tmp_path / "transactions.csv"
    write_csv(csv_path, [["2024-01-02", "BOS", "", "Player One", "sore knee"]])

    with Session(engine, expire_on_commit=False) as session:
        with pytest.raises(ValueError, match="unsupported source type"):
            import_historical_csv(session, "guessed", csv_path)


def test_cli_prints_counts_and_repeated_run_skips_duplicates(tmp_path, engine, monkeypatch, capsys):
    csv_path = tmp_path / "transactions.csv"
    write_csv(csv_path, [["2024-01-02", "BOS", "", "Player One", "sore knee"]])
    monkeypatch.setattr(import_csv_job, "build_engine", lambda: engine)

    assert import_csv_job.main(["--source-type", "il", str(csv_path)]) == 0
    assert capsys.readouterr().out.strip() == "read=1 inserted=1 skipped=0 invalid=0"

    assert import_csv_job.main(["--source-type", "il", str(csv_path)]) == 0
    assert capsys.readouterr().out.strip() == "read=1 inserted=0 skipped=1 invalid=0"
