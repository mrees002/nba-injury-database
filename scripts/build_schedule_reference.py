#!/usr/bin/env python3
"""Build a normalized NBA schedule reference CSV from local source files.

Accepts one or more CSV or JSON files containing game schedule data and
normalises them into ``data/reference/nba_schedule_games.csv``.

No network requests are made.  The database is not touched.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from app.nba.normalize import TEAM_ABBREVIATIONS, canonical_team_name
from app.nba.seasons import get_official_nba_season, get_preseason_nba_season

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "data" / "reference" / "nba_schedule_games.csv"

VALID_SEASON_TYPES = frozenset({"preseason", "regular", "play_in", "playoffs"})

# Reverse map: canonical team name -> abbreviation
_NAME_TO_ABBR: dict[str, str] = {v: k for k, v in TEAM_ABBREVIATIONS.items()}

OUTPUT_COLUMNS = ["season", "game_date", "season_type", "away_team", "home_team", "matchup"]


def _parse_date(raw: str) -> date:
    """Parse a date string supporting ISO and common US formats."""
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: {raw!r}")


def _resolve_team_input(raw: str) -> str:
    """Resolve a team name or abbreviation to the canonical full name."""
    stripped = raw.strip()
    if stripped in TEAM_ABBREVIATIONS:
        return TEAM_ABBREVIATIONS[stripped]
    return canonical_team_name(stripped)


def _resolve_abbr(team_name: str) -> str:
    """Return the 3-letter abbreviation for a canonical team name."""
    canonical = canonical_team_name(team_name)
    abbr = _NAME_TO_ABBR.get(canonical)
    if abbr is None:
        raise ValueError(f"Unknown team: {team_name!r} (canonical: {canonical!r})")
    return abbr


def _validate_season_type(value: str) -> str:
    value = value.strip().lower().replace(" ", "").replace("-", "_")
    if value not in VALID_SEASON_TYPES:
        raise ValueError(
            f"Invalid season_type {value!r}; expected one of {sorted(VALID_SEASON_TYPES)}"
        )
    return value


def _normalise_row(
    row: dict[str, str],
    default_season_type: str | None,
) -> dict[str, str]:
    """Normalise a single source row into the output schema."""
    game_date_raw = row.get("game_date") or row.get("date") or row.get("Date")
    if not game_date_raw:
        raise ValueError("Missing 'game_date' column")

    away_raw = row.get("away_team") or row.get("away") or row.get("Away")
    home_raw = row.get("home_team") or row.get("home") or row.get("Home")
    if not away_raw or not home_raw:
        raise ValueError(f"Missing team columns in row: {row}")

    away_canonical = _resolve_team_input(away_raw)
    home_canonical = _resolve_team_input(home_raw)

    away_abbr = _resolve_abbr(away_canonical)
    home_abbr = _resolve_abbr(home_canonical)

    game_date = _parse_date(game_date_raw)

    season_type_raw = (
        row.get("season_type")
        or row.get("type")
        or row.get("Type")
        or default_season_type
    )
    if not season_type_raw:
        raise ValueError(
            f"No season_type for game on {game_date} ({away_abbr}@{home_abbr}); "
            "provide it in the source file or use --default-season-type"
        )
    season_type = _validate_season_type(season_type_raw)

    if season_type == "preseason":
        season = get_preseason_nba_season(game_date)
    else:
        season = get_official_nba_season(game_date)

    matchup = f"{away_abbr}@{home_abbr}"

    return {
        "season": season,
        "game_date": game_date.isoformat(),
        "season_type": season_type,
        "away_team": away_canonical,
        "home_team": home_canonical,
        "matchup": matchup,
    }


def _load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"Empty CSV file: {path}")
        return [dict(row) for row in reader]


def _load_json(path: Path) -> list[dict[str, str]]:
    raw = json.loads(path.read_text())
    if isinstance(raw, dict):
        raw = raw.get("games") or raw.get("schedule") or raw.get("data")
        if raw is None:
            raise ValueError(
                f"Expected a JSON list of games in {path} "
                "(or a dict with 'games'/'schedule'/'data' key)"
            )
    if not isinstance(raw, list):
        raise ValueError(f"Expected a JSON list of games in {path}")
    return [dict(item) for item in raw]


def build_schedule(
    source_paths: list[Path],
    default_season_type: str | None = None,
) -> list[dict[str, str]]:
    """Read source files, normalise, validate, and return rows."""
    all_rows: list[dict[str, str]] = []
    for path in source_paths:
        if path.suffix.lower() == ".json":
            raw_rows = _load_json(path)
        elif path.suffix.lower() == ".csv":
            raw_rows = _load_csv(path)
        else:
            raise ValueError(f"Unsupported file type: {path.suffix}")

        for raw in raw_rows:
            all_rows.append(_normalise_row(raw, default_season_type))

    seen: Counter[tuple[str, str]] = Counter()
    for row in all_rows:
        key = (row["game_date"], row["matchup"])
        seen[key] += 1

    duplicates = {k: v for k, v in seen.items() if v > 1}
    if duplicates:
        lines = [
            f"  {date_str} {matchup}: {count} occurrences"
            for (date_str, matchup), count in sorted(duplicates.items())
        ]
        raise ValueError(
            "Duplicate (game_date, matchup) rows found:\n" + "\n".join(lines)
        )

    all_rows.sort(key=lambda r: (r["season"], r["game_date"], r["matchup"]))
    return all_rows


def write_reference(rows: list[dict[str, str]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a normalised NBA schedule reference CSV.",
    )
    parser.add_argument(
        "sources",
        nargs="+",
        type=Path,
        help="Local CSV or JSON schedule source files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output CSV path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--default-season-type",
        choices=sorted(VALID_SEASON_TYPES),
        help="Season type to apply when the source file does not specify one.",
    )
    args = parser.parse_args(argv)

    for src in args.sources:
        if not src.exists():
            print(f"Error: source file not found: {src}", file=sys.stderr)
            return 1

    try:
        rows = build_schedule(args.sources, args.default_season_type)
    except (ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    write_reference(rows, args.output)
    print(f"Wrote {len(rows)} games to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
