#!/usr/bin/env python3
"""Audit player-observation coverage of a schedule reference against the database.

Compares ``data/reference/nba_schedule_games.csv`` against the canonical
``NBAReportEntry`` layer and reports coverage by season and season_type.

Read-only database access.  No mutations.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.nba import NBAReportEntry

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REFERENCE = REPO_ROOT / "data" / "reference" / "nba_schedule_games.csv"

VALID_SEASON_TYPES = frozenset({"preseason", "regular", "play_in", "playoffs"})


def load_reference(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    for row in rows:
        st = row["season_type"]
        if st not in VALID_SEASON_TYPES:
            raise ValueError(f"Invalid season_type {st!r} in reference file")
    return rows


def _load_reference_by_season_type(
    path: Path,
) -> dict[str, dict[str, set[tuple[str, str]]]]:
    """Return {season: {season_type: {(game_date, matchup)}}}."""
    ref = load_reference(path)
    result: dict[str, dict[str, set[tuple[str, str]]]] = defaultdict(lambda: defaultdict(set))
    for row in ref:
        result[row["season"]][row["season_type"]].add((row["game_date"], row["matchup"]))
    return result


def _load_reference_details(
    path: Path,
) -> dict[tuple[str, str], dict[str, str]]:
    """Return {(game_date, matchup): {away_team, home_team}} lookup."""
    ref = load_reference(path)
    return {
        (row["game_date"], row["matchup"]): {
            "away_team": row["away_team"],
            "home_team": row["home_team"],
        }
        for row in ref
    }


def _load_reference_by_month(
    path: Path,
) -> dict[str, dict[str, dict[str, set[tuple[str, str]]]]]:
    """Return {season: {season_type: {month_key: {(game_date, matchup)}}}}."""
    ref = load_reference(path)
    result: dict[str, dict[str, dict[str, set[tuple[str, str]]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(set))
    )
    for row in ref:
        month_key = row["game_date"][:7]  # YYYY-MM
        result[row["season"]][row["season_type"]][month_key].add(
            (row["game_date"], row["matchup"])
        )
    return result


def query_observed_games(session: Session) -> set[tuple[str, str]]:
    """Return flat set of (game_date, matchup) from player-type NBAReportEntry rows."""
    rows = session.execute(
        select(
            NBAReportEntry.game_date,
            NBAReportEntry.matchup,
        )
        .where(NBAReportEntry.entry_type == "player")
        .distinct()
    ).all()
    return {(game_date.isoformat(), matchup) for game_date, matchup in rows}


def query_observed_games_with_all_available(session: Session) -> set[tuple[str, str]]:
    """Return flat set of (game_date, matchup) with all_available entries."""
    rows = session.execute(
        select(
            NBAReportEntry.game_date,
            NBAReportEntry.matchup,
        )
        .where(NBAReportEntry.entry_type == "all_available")
        .distinct()
    ).all()
    return {(game_date.isoformat(), matchup) for game_date, matchup in rows}


def _pct(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator * 100 / denominator, 1)


def audit(
    reference_path: Path = DEFAULT_REFERENCE,
    database_url: str | None = None,
) -> dict[str, object]:
    """Run the full coverage audit and return a structured result dict."""
    ref_by_st = _load_reference_by_season_type(reference_path)
    ref_by_month = _load_reference_by_month(reference_path)
    details = _load_reference_details(reference_path)

    settings = get_settings()
    url = database_url or settings.database_url
    engine = create_engine(url)

    with Session(engine) as session:
        observed = query_observed_games(session)
        all_available = query_observed_games_with_all_available(session)

    covered = observed | all_available

    report: dict[str, object] = {"seasons": {}}

    for season in sorted(ref_by_st.keys()):
        season_ref = ref_by_st.get(season, {})
        season_months = ref_by_month.get(season, {})

        season_report: dict[str, object] = {}
        for st in sorted(season_ref.keys()):
            scheduled = season_ref.get(st, set())
            player_obs_in_bucket = scheduled & observed
            all_avail_in_bucket = scheduled & all_available
            covered_in_bucket = scheduled & covered
            missing = scheduled - covered_in_bucket
            missing_details = [
                {
                    "game_date": game_date,
                    "matchup": matchup,
                    "away_team": details[(game_date, matchup)]["away_team"],
                    "home_team": details[(game_date, matchup)]["home_team"],
                }
                for game_date, matchup in sorted(missing)
            ]

            months_detail: dict[str, dict[str, int]] = {}
            for month_key in sorted(season_months.get(st, {}).keys()):
                month_scheduled = season_months[st][month_key]
                month_covered = month_scheduled & covered
                month_player_obs = month_scheduled & observed
                month_missing = month_scheduled - month_covered
                months_detail[month_key] = {
                    "scheduled": len(month_scheduled),
                    "player_observations": len(month_player_obs),
                    "missing": len(month_missing),
                }

            season_report[st] = {
                "scheduled_games": len(scheduled),
                "games_with_player_observations": len(player_obs_in_bucket),
                "games_with_all_available": len(all_avail_in_bucket),
                "player_observation_coverage_pct": _pct(len(player_obs_in_bucket), len(scheduled)),
                "total_covered_pct": _pct(len(covered_in_bucket), len(scheduled)),
                "missing_games": len(missing),
                "missing_game_keys": sorted(missing),
                "missing_details": missing_details,
                "missing_by_month": months_detail,
            }

        report["seasons"][season] = season_report

    return report


def _print_report(report: dict[str, object]) -> None:
    seasons = report["seasons"]
    for season in sorted(seasons):
        print(f"\n{'=' * 60}")
        print(f"Season: {season}")
        print(f"{'=' * 60}")
        for st, metrics in sorted(seasons[season].items()):
            print(f"\n  [{st.upper()}]")
            print(f"    Scheduled games:            {metrics['scheduled_games']}")
            print(
                f"    Player-observation games:   "
                f"{metrics['games_with_player_observations']} "
                f"({metrics['player_observation_coverage_pct']}%)"
            )
            print(f"    ALL PLAYERS AVAILABLE games: {metrics['games_with_all_available']}")
            print(f"    Total covered:              {metrics['total_covered_pct']}%")
            print(f"    Missing games:              {metrics['missing_games']}")
            if metrics["missing_by_month"]:
                print("    Missing by month:")
                for month, detail in sorted(metrics["missing_by_month"].items()):
                    print(
                        f"      {month}: {detail['missing']}/{detail['scheduled']} "
                        f"({detail['player_observations']} with observations)"
                    )
            missing_details = metrics.get("missing_details", [])
            if missing_details:
                print("    Missing games:")
                for g in missing_details:
                    print(
                        f"      {g['game_date']}  {g['matchup']}  "
                        f"{g['away_team']} @ {g['home_team']}"
                    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Audit player-observation coverage against a schedule reference.",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        default=DEFAULT_REFERENCE,
        help=f"Reference schedule CSV (default: {DEFAULT_REFERENCE})",
    )
    parser.add_argument(
        "--database-url",
        help="Override DATABASE_URL (default: from app config / env)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output raw JSON instead of formatted text.",
    )
    args = parser.parse_args(argv)

    if not args.reference.exists():
        print(f"Error: reference file not found: {args.reference}", file=sys.stderr)
        return 1

    try:
        report = audit(args.reference, args.database_url)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.output_json:
        import json

        print(json.dumps(report, indent=2, default=str))
    else:
        _print_report(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
