from __future__ import annotations

import json

from app.db.session import build_engine, build_session_factory
from app.nba.episode_audit import build_episode_audit


def main() -> None:
    session_factory = build_session_factory(build_engine())
    with session_factory() as session:
        report = build_episode_audit(session)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
