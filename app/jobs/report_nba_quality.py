from __future__ import annotations

import json

from app.db.session import build_engine, build_session_factory
from app.nba.quality import build_quality_report


def main() -> None:
    session_factory = build_session_factory(build_engine())
    with session_factory() as session:
        report = build_quality_report(session)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
