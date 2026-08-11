from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.db.session import build_engine, build_session_factory
from app.services.csv_import import SOURCE_TYPES, CSVValidationError, import_historical_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import a legacy NBA transaction CSV")
    parser.add_argument(
        "--source-type",
        required=True,
        choices=sorted(SOURCE_TYPES),
        help="Explicit source represented by this file",
    )
    parser.add_argument("path", type=Path, help="CSV file in the legacy five-column format")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    engine = None

    try:
        engine = build_engine()
        session_factory = build_session_factory(engine)
        with session_factory() as session:
            result = import_historical_csv(session, args.source_type, args.path)
    except (CSVValidationError, OSError, SQLAlchemyError, ValueError) as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()

    print(
        f"read={result.read} inserted={result.inserted} "
        f"skipped={result.skipped} invalid={result.invalid}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
