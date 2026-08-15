from __future__ import annotations

import argparse

from app.db.session import build_engine, build_session_factory
from app.nba.episodes import episode_semantic_digest, rebuild_injury_episodes


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild official-NBA injury episodes")
    parser.parse_args()
    session_factory = build_session_factory(build_engine())
    with session_factory() as session:
        result = rebuild_injury_episodes(session)
        digest = episode_semantic_digest(session)
    print(
        f"observations={result.observations} episodes={result.episodes} "
        f"players={result.players} semantic_digest={digest}"
    )


if __name__ == "__main__":
    main()
