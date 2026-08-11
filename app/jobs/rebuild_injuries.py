from __future__ import annotations

import sys

from sqlalchemy.exc import SQLAlchemyError

from app.db.session import build_engine, build_session_factory
from app.processing import rebuild_injuries


def main() -> int:
    engine = None
    try:
        engine = build_engine()
        session_factory = build_session_factory(engine)
        with session_factory() as session:
            result = rebuild_injuries(session)
    except SQLAlchemyError as exc:
        print(f"Rebuild failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()

    print(f"raw_rows={result.raw_rows} injury_rows={result.injury_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
