#!/usr/bin/env python3
"""Read-only experiment: compare BALLDONTLIE NBA game data against nba_schedule_games.

Determines whether the BALLDONTLIE free Games API can replace stats.nba.com
as the source for nba_schedule_games.  Season 2025-26 first.

No database writes.  No production code changes.
"""

from __future__ import annotations

import csv
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_CSV = REPO_ROOT / "data" / "reference" / "nba_schedule_games.csv"

SEASON = "2025-26"
BDL_SEASON = 2025  # BALLDONTLIE uses the start year

TEAM_ABBREVIATIONS = {
    "ATL": "Atlanta Hawks",
    "BKN": "Brooklyn Nets",
    "BOS": "Boston Celtics",
    "CHA": "Charlotte Hornets",
    "CHI": "Chicago Bulls",
    "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks",
    "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors",
    "HOU": "Houston Rockets",
    "IND": "Indiana Pacers",
    "LAC": "LA Clippers",
    "LAL": "Los Angeles Lakers",
    "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks",
    "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans",
    "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic",
    "PHI": "Philadelphia 76ers",
    "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers",
    "SAC": "Sacramento Kings",
    "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz",
    "WAS": "Washington Wizards",
}

TEAM_ALIASES = {
    "Los Angeles Clippers": "LA Clippers",
}


def canonical_team_name(raw_name: str) -> str:
    normalized = " ".join(raw_name.split())
    return TEAM_ALIASES.get(normalized, normalized)


@dataclass(frozen=True)
class BDLGame:
    bdl_id: int
    game_date: date
    away_abbr: str
    home_abbr: str
    matchup: str
    postseason: bool


def normalize_bdl_game(raw: dict[str, Any]) -> BDLGame | None:
    """Normalize a single BALLDONTLIE game record into a BDLGame.

    Returns None for non-NBA teams or unrecognised abbreviations.
    """
    home_team = raw.get("home_team", {})
    visitor_team = raw.get("visitor_team", {})

    home_abbr = home_team.get("abbreviation", "")
    away_abbr = visitor_team.get("abbreviation", "")

    if home_abbr not in TEAM_ABBREVIATIONS or away_abbr not in TEAM_ABBREVIATIONS:
        return None

    game_date_str = raw.get("date", "")
    game_date = datetime.strptime(game_date_str, "%Y-%m-%d").date()

    matchup = f"{away_abbr}@{home_abbr}"

    return BDLGame(
        bdl_id=raw["id"],
        game_date=game_date,
        away_abbr=away_abbr,
        home_abbr=home_abbr,
        matchup=matchup,
        postseason=raw.get("postseason", False),
    )


def fetch_all_bdl_games(api_key: str, season: int) -> list[BDLGame]:
    """Fetch all NBA games for a BALLDONTLIE season with cursor pagination.

    Respects the free-tier limit of 5 requests/minute (12s between requests).
    """
    url = "https://api.balldontlie.io/v1/games"
    headers = {"Authorization": api_key}
    params: dict[str, Any] = {
        "seasons[]": season,
        "per_page": 100,
    }

    all_games: list[BDLGame] = []
    cursor: int | None = None
    page = 0

    with httpx.Client(timeout=30) as client:
        while True:
            page += 1
            if cursor is not None:
                params["cursor"] = cursor
            elif "cursor" in params:
                del params["cursor"]

            resp = client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            body = resp.json()

            for raw_game in body.get("data", []):
                normalized = normalize_bdl_game(raw_game)
                if normalized is not None:
                    all_games.append(normalized)

            meta = body.get("meta", {})
            cursor = meta.get("next_cursor")

            print(f"  Page {page}: fetched {len(body.get('data', []))} games "
                  f"(total normalised: {len(all_games)})")

            if cursor is None:
                break

            # Respect free-tier rate limit: 5 req/min → at least 12s between requests
            time.sleep(12)

    return all_games


def load_reference_games(path: Path, season: str) -> list[dict[str, str]]:
    """Load nba_schedule_games.csv rows for the given season."""
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        return [row for row in reader if row["season"] == season]


def build_ref_lookup(
    ref_rows: list[dict[str, str]],
) -> dict[tuple[str, str], dict[str, str]]:
    """Build {(game_date, matchup): row} lookup from reference rows."""
    return {
        (row["game_date"], row["matchup"]): row for row in ref_rows
    }


def build_bdl_lookup(
    bdl_games: list[BDLGame],
) -> dict[tuple[str, str], BDLGame]:
    """Build {(game_date_str, matchup): BDLGame} lookup."""
    return {
        (g.game_date.isoformat(), g.matchup): g for g in bdl_games
    }


def compare(
    ref_rows: list[dict[str, str]],
    bdl_games: list[BDLGame],
) -> dict[str, Any]:
    """Run the full comparison and return a structured report."""
    ref_lookup = build_ref_lookup(ref_rows)
    bdl_lookup = build_bdl_lookup(bdl_games)

    ref_keys = set(ref_lookup.keys())
    bdl_keys = set(bdl_lookup.keys())

    exact_matches = ref_keys & bdl_keys
    missing_from_bdl = ref_keys - bdl_keys
    extra_in_bdl = bdl_keys - ref_keys

    # Duplicate detection within each source
    ref_date_matchup_pairs = [(row["game_date"], row["matchup"]) for row in ref_rows]
    ref_pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    for pair in ref_date_matchup_pairs:
        ref_pair_counts[pair] += 1
    ref_duplicates = [k for k, c in ref_pair_counts.items() if c > 1]

    bdl_date_matchup_pairs = [(g.game_date.isoformat(), g.matchup) for g in bdl_games]
    bdl_pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    for pair in bdl_date_matchup_pairs:
        bdl_pair_counts[pair] += 1
    bdl_duplicates = [k for k, c in bdl_pair_counts.items() if c > 1]

    # Postseason breakdown by our season_type
    postseason_by_type: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in ref_rows:
        st = row["season_type"]
        key = (row["game_date"], row["matchup"])
        if key in bdl_lookup:
            bdl_game = bdl_lookup[key]
            label = "postseason=true" if bdl_game.postseason else "postseason=false"
            postseason_by_type[st][label] += 1
        else:
            postseason_by_type[st]["missing_from_bdl"] += 1

    # Play-In and Preseason details
    playin_games: list[dict[str, str]] = []
    preseason_games: list[dict[str, str]] = []
    for row in ref_rows:
        entry = {
            "date": row["game_date"],
            "matchup": row["matchup"],
            "our_season_type": row["season_type"],
        }
        key = (row["game_date"], row["matchup"])
        if key in bdl_lookup:
            entry["bdl_postseason"] = str(bdl_lookup[key].postseason)
        else:
            entry["bdl_postseason"] = "MISSING"
        if row["season_type"] == "play_in":
            playin_games.append(entry)
        elif row["season_type"] == "preseason":
            preseason_games.append(entry)

    return {
        "total_bdl_games": len(bdl_games),
        "total_ref_games": len(ref_rows),
        "exact_matches": len(exact_matches),
        "missing_from_bdl": sorted(missing_from_bdl),
        "extra_in_bdl": sorted(extra_in_bdl),
        "ref_duplicates": ref_duplicates,
        "bdl_duplicates": bdl_duplicates,
        "postseason_by_type": dict(postseason_by_type),
        "playin_games": playin_games,
        "preseason_games": preseason_games,
    }


def print_report(report: dict[str, Any]) -> None:
    print("=" * 70)
    print(f"BALLDONTLIE vs nba_schedule_games  —  Season {SEASON}")
    print("=" * 70)

    print(f"\nTotal BALLDONTLIE games (NBA teams only): {report['total_bdl_games']}")
    print(f"Total reference schedule games:            {report['total_ref_games']}")
    print(f"Exact matchup/date matches:                {report['exact_matches']}")

    missing = report["missing_from_bdl"]
    print(f"\nGames missing from BALLDONTLIE ({len(missing)}):")
    for key in missing:
        print(f"  {key[0]}  {key[1]}")

    extra = report["extra_in_bdl"]
    print(f"\nExtra BALLDONTLIE games not in reference ({len(extra)}):")
    for key in extra:
        print(f"  {key[0]}  {key[1]}")

    if report["ref_duplicates"]:
        print(f"\nDuplicate date/matchup in reference ({len(report['ref_duplicates'])}):")
        for key in report["ref_duplicates"]:
            print(f"  {key[0]}  {key[1]}")
    else:
        print("\nNo duplicate date/matchup rows in reference.")

    if report["bdl_duplicates"]:
        print(f"\nDuplicate date/matchup in BALLDONTLIE ({len(report['bdl_duplicates'])}):")
        for key in report["bdl_duplicates"]:
            print(f"  {key[0]}  {key[1]}")
    else:
        print("No duplicate date/matchup rows in BALLDONTLIE.")

    print("\n" + "-" * 70)
    print("Postseason flag breakdown by our season_type:")
    print("-" * 70)
    for st in ["preseason", "regular", "play_in", "playoffs"]:
        counts = report["postseason_by_type"].get(st, {})
        total = sum(counts.values())
        print(f"\n  [{st}]  (matched: {total})")
        for label in ["postseason=true", "postseason=false", "missing_from_bdl"]:
            if label in counts:
                print(f"    {label}: {counts[label]}")

    print("\n" + "-" * 70)
    print("Play-In games:")
    print("-" * 70)
    if report["playin_games"]:
        for g in report["playin_games"]:
            print(f"  {g['date']}  {g['matchup']:>12}  our={g['our_season_type']:>10}  "
                  f"bdl_postseason={g['bdl_postseason']}")
    else:
        print("  (none)")

    print("\n" + "-" * 70)
    print("Preseason games:")
    print("-" * 70)
    if report["preseason_games"]:
        for g in report["preseason_games"]:
            print(f"  {g['date']}  {g['matchup']:>12}  our={g['our_season_type']:>10}  "
                  f"bdl_postseason={g['bdl_postseason']}")
    else:
        print("  (none)")

    print("\n" + "-" * 70)
    print("Season-type reproduction assessment:")
    print("-" * 70)
    _print_assessment(report)


def _print_assessment(report: dict[str, Any]) -> None:
    """Determine whether season_type can be reproduced from postseason + date boundaries."""
    post_by_type = report["postseason_by_type"]

    # Check if postseason=true maps cleanly to playoffs only
    post_true_types = set()
    post_false_types = set()
    for st, counts in post_by_type.items():
        if counts.get("postseason=true", 0) > 0:
            post_true_types.add(st)
        if counts.get("postseason=false", 0) > 0:
            post_false_types.add(st)

    print(f"\n  postseason=true appears in our types: {sorted(post_true_types)}")
    print(f"  postseason=false appears in our types: {sorted(post_false_types)}")

    issues = []

    # Check: does postseason=true EVER appear in non-playoffs types?
    if post_true_types - {"playoffs"}:
        issues.append(
            f"  WARNING: postseason=true appears outside playoffs: "
            f"{sorted(post_true_types - {'playoffs'})}"
        )

    # Check: does postseason=false appear in playoffs?
    if "playoffs" in post_false_types:
        issues.append(
            "  WARNING: postseason=false appears in our playoffs games"
        )

    # Check: play_in games — what's their postseason value?
    playin = post_by_type.get("play_in", {})
    playin_post_true = playin.get("postseason=true", 0)
    playin_post_false = playin.get("postseason=false", 0)
    playin_missing = playin.get("missing_from_bdl", 0)

    if playin_post_true > 0 and playin_post_false > 0:
        issues.append(
            "  WARNING: play_in games have mixed postseason values in BALLDONTLIE"
        )
    elif playin_post_true > 0:
        issues.append(
            "  NOTE: play_in games are marked postseason=true in BALLDONTLIE"
        )
    elif playin_post_false > 0:
        issues.append(
            "  NOTE: play_in games are marked postseason=false in BALLDONTLIE"
        )

    # Check: preseason — what's their postseason value?
    pre = post_by_type.get("preseason", {})
    pre_post_true = pre.get("postseason=true", 0)
    pre_missing = pre.get("missing_from_bdl", 0)

    if pre_post_true > 0:
        issues.append(
            "  NOTE: preseason games are marked postseason=true in BALLDONTLIE"
        )

    for issue in issues:
        print(issue)

    if not issues:
        print("  No issues found — postseason flag maps cleanly.")

    # Summary judgment
    print("\n  SUMMARY:")
    can_reproduce = True

    if post_true_types - {"playoffs"}:
        print("  - Cannot reproduce season_type from postseason alone:")
        print("    postseason=true leaks into non-playoffs types.")
        can_reproduce = False

    if playin_post_true > 0 and playin_post_false > 0:
        print("  - play_in games have inconsistent postseason values.")
        can_reproduce = False

    if pre_missing > 0 or playin_missing > 0:
        print(f"  - BALLDONTLIE is missing {pre_missing} preseason and "
              f"{playin_missing} play_in games.")
        can_reproduce = False

    if can_reproduce:
        print("  - season_type CAN be reproduced using postseason + date boundaries.")
    else:
        print("  - season_type CANNOT be reliably reproduced from BALLDONTLIE alone.")
        print("    A lookup table or date-boundary rules would be needed for")
        print("    preseason and play_in classification.")


def main() -> int:
    api_key = os.environ.get("BALLDONTLIE_API_KEY", "")
    if not api_key:
        print("Error: BALLDONTLIE_API_KEY environment variable not set.", file=sys.stderr)
        print("Set it with:  export BALLDONTLIE_API_KEY='your-key-here'", file=sys.stderr)
        return 1

    if not REFERENCE_CSV.exists():
        print(f"Error: reference file not found: {REFERENCE_CSV}", file=sys.stderr)
        return 1

    print(f"Loading reference schedule for season {SEASON} ...")
    ref_rows = load_reference_games(REFERENCE_CSV, SEASON)
    print(f"  {len(ref_rows)} reference games loaded.")

    print(f"\nFetching BALLDONTLIE games for season {BDL_SEASON} ...")
    try:
        bdl_games = fetch_all_bdl_games(api_key, BDL_SEASON)
    except httpx.HTTPStatusError as exc:
        print(f"HTTP error: {exc.response.status_code} {exc.response.text}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error fetching BALLDONTLIE data: {exc}", file=sys.stderr)
        return 1

    print("\nRunning comparison ...")
    report = compare(ref_rows, bdl_games)
    print_report(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
