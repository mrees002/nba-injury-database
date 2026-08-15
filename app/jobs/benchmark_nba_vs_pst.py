from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from app.db.session import build_engine, build_session_factory
from app.nba.benchmark import (
    DEFAULT_OUTPUT_DIRECTORY,
    build_benchmark_result,
    write_benchmark_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare official-NBA episodes with PST benchmark")
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
        help=f"artifact directory (default: {DEFAULT_OUTPUT_DIRECTORY})",
    )
    parser.add_argument(
        "--player",
        action="append",
        help="restrict to an exact normalized player name; repeat for a bounded sample",
    )
    args = parser.parse_args()
    session_factory = build_session_factory(build_engine())
    with session_factory() as session:
        result = build_benchmark_result(session, args.start_date, args.end_date, args.player)
    paths = write_benchmark_artifacts(result, args.output_dir)
    output = {
        **result.summary,
        "output_files": {name: str(path) for name, path in paths.items()},
    }
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
