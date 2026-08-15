from __future__ import annotations

import json
from dataclasses import asdict

from app.db.session import build_engine, build_session_factory
from app.nba.reclassify import reclassify_conditions


def main() -> None:
    session_factory = build_session_factory(build_engine())
    with session_factory() as session:
        result = reclassify_conditions(session)
    print(json.dumps(asdict(result), sort_keys=True))


if __name__ == "__main__":
    main()
