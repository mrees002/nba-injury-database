from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RawTransaction

REQUIRED_COLUMNS = ("Date", "Team", "Acquired", "Relinquished", "Notes")
SOURCE_TYPES = frozenset({"il", "missed_game"})
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")
LOOKUP_BATCH_SIZE = 500


class CSVValidationError(ValueError):
    """Raised when a CSV cannot be interpreted as a legacy transaction file."""


@dataclass(frozen=True)
class ImportResult:
    read: int
    inserted: int
    skipped: int
    invalid: int


@dataclass(frozen=True)
class ParsedTransaction:
    transaction_date: date
    team: str
    acquired: str
    relinquished: str
    notes: str
    source_row_key: str


def parse_transaction_date(value: str) -> date:
    normalized = value.strip()
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(normalized, date_format).date()
        except ValueError:
            continue
    accepted = ", ".join(DATE_FORMATS)
    raise ValueError(f"unsupported date {value!r}; expected one of: {accepted}")


def build_source_row_key(
    source_type: str,
    transaction_date: date,
    team: str,
    acquired: str,
    relinquished: str,
    notes: str,
) -> str:
    payload = [
        "v1",
        source_type,
        transaction_date.isoformat(),
        team,
        acquired,
        relinquished,
        notes,
    ]
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _validate_source_type(source_type: str) -> None:
    if source_type not in SOURCE_TYPES:
        allowed = ", ".join(sorted(SOURCE_TYPES))
        raise ValueError(f"unsupported source type {source_type!r}; expected one of: {allowed}")


def _read_transactions(
    path: Path,
    source_type: str,
) -> tuple[list[ParsedTransaction], int, int]:
    transactions: list[ParsedTransaction] = []
    rows_read = 0
    invalid_rows = 0

    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file, strict=True)
        if reader.fieldnames is None:
            raise CSVValidationError("CSV is empty or does not contain a header row")

        missing_columns = [column for column in REQUIRED_COLUMNS if column not in reader.fieldnames]
        if missing_columns:
            missing = ", ".join(missing_columns)
            raise CSVValidationError(f"CSV is missing required columns: {missing}")

        try:
            for row in reader:
                rows_read += 1
                if None in row or any(row.get(column) is None for column in REQUIRED_COLUMNS):
                    invalid_rows += 1
                    continue

                try:
                    transaction_date = parse_transaction_date(row["Date"])
                except ValueError:
                    invalid_rows += 1
                    continue

                team = row["Team"]
                acquired = row["Acquired"]
                relinquished = row["Relinquished"]
                notes = row["Notes"]
                transactions.append(
                    ParsedTransaction(
                        transaction_date=transaction_date,
                        team=team,
                        acquired=acquired,
                        relinquished=relinquished,
                        notes=notes,
                        source_row_key=build_source_row_key(
                            source_type,
                            transaction_date,
                            team,
                            acquired,
                            relinquished,
                            notes,
                        ),
                    )
                )
        except csv.Error as exc:
            raise CSVValidationError(f"malformed CSV near line {reader.line_num}: {exc}") from exc

    return transactions, rows_read, invalid_rows


def _batched(values: list[str], batch_size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), batch_size):
        yield values[start : start + batch_size]


def import_historical_csv(
    session: Session,
    source_type: str,
    path: str | Path,
) -> ImportResult:
    """Import one legacy CSV in a single database transaction."""

    _validate_source_type(source_type)
    csv_path = Path(path)
    parsed, rows_read, invalid_rows = _read_transactions(csv_path, source_type)

    unique_keys = list(dict.fromkeys(row.source_row_key for row in parsed))
    existing_keys: set[str] = set()
    for key_batch in _batched(unique_keys, LOOKUP_BATCH_SIZE):
        existing_keys.update(
            session.scalars(
                select(RawTransaction.source_row_key).where(
                    RawTransaction.source_type == source_type,
                    RawTransaction.source_row_key.in_(key_batch),
                )
            )
        )

    new_transactions: list[RawTransaction] = []
    seen_keys: set[str] = set()
    skipped_rows = 0
    for row in parsed:
        if row.source_row_key in existing_keys or row.source_row_key in seen_keys:
            skipped_rows += 1
            continue

        seen_keys.add(row.source_row_key)
        new_transactions.append(
            RawTransaction(
                source_type=source_type,
                transaction_date=row.transaction_date,
                team=row.team,
                acquired=row.acquired,
                relinquished=row.relinquished,
                notes=row.notes,
                source_url=None,
                source_row_key=row.source_row_key,
            )
        )

    try:
        session.add_all(new_transactions)
        session.commit()
    except Exception:
        session.rollback()
        raise

    return ImportResult(
        read=rows_read,
        inserted=len(new_transactions),
        skipped=skipped_rows,
        invalid=invalid_rows,
    )
