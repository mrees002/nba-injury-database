#!/usr/bin/env python3
"""Fetch NBA game schedules from the stats LeagueGameLog endpoint.

One-off script that pulls historical season data and writes local source files
for the existing ``build_schedule_reference.py`` workflow.  No database or
injury-report code is touched.

Usage::

    python scripts/fetch_nba_schedule_reference.py

Results land under ``data/reference/source/`` as raw per-season/type CSVs and a
single normalised reference CSV.
"""

from __future__ import annotations

import csv
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "data" / "reference" / "source"
OUTPUT_CSV = REPO_ROOT / "data" / "reference" / "nba_schedule_reference.csv"

NBA_ABBREVIATIONS = frozenset(
    {
        "ATL", "BKN", "BOS", "CHA", "CHI", "CLE", "DAL", "DEN", "DET",
        "GSW", "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN",
        "NOP", "NYK", "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS",
        "TOR", "UTA", "WAS",
    }
)

SEASONS = [
    "2018-19",
    "2019-20",
    "2020-21",
    "2021-22",
    "2022-23",
    "2023-24",
    "2024-25",
    "2025-26",
]

SEASON_TYPES = [
    "Pre Season",
    "Regular Season",
    "PlayIn",
    "Playoffs",
]

# Map NBA API season_type string -> build_schedule_reference canonical label
_SEASON_TYPE_LABEL: dict[str, str] = {
    "Pre Season": "preseason",
    "Regular Season": "regular",
    "PlayIn": "play_in",
    "Playoffs": "playoffs",
}

REQUEST_DELAY_SECONDS = 1.5

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MATCHUP_RE = re.compile(r"^(\w+)\s+(?:@|vs\.)\s+(\w+)$")


@dataclass(frozen=True)
class Game:
    game_id: str
    season: str
    game_date: str
    season_type: str
    away_abbr: str
    home_abbr: str


def _parse_matchup(matchup: str) -> tuple[str, str]:
    """Return ``(away_abbr, home_abbr)`` from a MATCHUP string."""
    m = _MATCHUP_RE.match(matchup.strip())
    if m is None:
        raise ValueError(f"Cannot parse MATCHUP: {matchup!r}")
    team_a, team_b = m.group(1), m.group(2)
    if matchup.strip().startswith(f"{team_a} @"):
        return team_a, team_b
    return team_b, team_a


def _game_date_iso(raw: str) -> str:
    """Normalise GAME_DATE to ``YYYY-MM-DD``."""
    raw = raw.strip()
    # NBA API already returns ISO-ish dates (2024-10-22) but guard against
    # alternate formats.
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: {raw!r}")


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def _fetch_one(
    season: str,
    season_type: str,
) -> list[dict[str, str]]:
    """Fetch rows for a single season/type from the NBA stats endpoint.

    Returns a list of raw dicts (one per team row).  Returns an empty list
    when the endpoint returns no data.
    """
    from nba_api.stats.endpoints.leaguegamelog import LeagueGameLog

    logger.info("Fetching %s / %s …", season, season_type)

    lg = LeagueGameLog(
        season=season,
        season_type_all_star=season_type,
        player_or_team_abbreviation="T",
        timeout=30,
    )
    df = lg.league_game_log.get_data_frame()
    if df.empty:
        logger.warning("No data for %s / %s", season, season_type)
        return []

    columns = [
        "SEASON_ID",
        "TEAM_ABBREVIATION",
        "TEAM_NAME",
        "GAME_ID",
        "GAME_DATE",
        "MATCHUP",
        "WL",
    ]
    subset = df[columns]
    return subset.to_dict(orient="records")


def _write_raw_csv(rows: list[dict[str, str]], path: Path) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _deduplicate(rows: list[dict[str, str]], season: str, season_type: str) -> list[Game]:
    """Collapse team-level rows into one row per GAME_ID.

    Non-NBA teams (e.g. preseason exhibition opponents) are dropped.
    """
    by_game: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_game.setdefault(row["GAME_ID"], []).append(row)

    label = _SEASON_TYPE_LABEL[season_type]
    games: list[Game] = []
    for game_id, team_rows in by_game.items():
        if len(team_rows) != 2:
            logger.debug(
                "Skipping game %s: expected 2 rows, got %d", game_id, len(team_rows),
            )
            continue

        all_abbrs = [r["TEAM_ABBREVIATION"] for r in team_rows]
        if not all(a in NBA_ABBREVIATIONS for a in all_abbrs):
            logger.debug(
                "Skipping game %s: non-NBA teams %s", game_id, all_abbrs,
            )
            continue

        matchup = team_rows[0]["MATCHUP"]
        try:
            away_abbr, home_abbr = _parse_matchup(matchup)
        except ValueError:
            logger.debug("Skipping game %s: unparseable matchup %r", game_id, matchup)
            continue

        game_date = _game_date_iso(team_rows[0]["GAME_DATE"])

        games.append(
            Game(
                game_id=game_id,
                season=season,
                game_date=game_date,
                season_type=label,
                away_abbr=away_abbr,
                home_abbr=home_abbr,
            )
        )

    games.sort(key=lambda g: (g.season, g.game_date, g.away_abbr, g.home_abbr))
    return games


def _write_reference_csv(games: list[Game], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["season", "game_date", "season_type", "away_team", "home_team"])
        for g in games:
            writer.writerow([g.season, g.game_date, g.season_type, g.away_abbr, g.home_abbr])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    all_games: list[Game] = []

    for season in SEASONS:
        for season_type in SEASON_TYPES:
            rows = _fetch_one(season, season_type)
            time.sleep(REQUEST_DELAY_SECONDS)

            slug = f"{season}_{season_type.lower().replace(' ', '_')}"
            raw_path = SOURCE_DIR / f"{slug}.csv"
            _write_raw_csv(rows, raw_path)
            if rows:
                logger.info("Saved %d raw rows → %s", len(rows), raw_path)

            games = _deduplicate(rows, season, season_type)
            all_games.extend(games)

    _write_reference_csv(all_games, OUTPUT_CSV)
    logger.info("Wrote %d games → %s", len(all_games), OUTPUT_CSV)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
