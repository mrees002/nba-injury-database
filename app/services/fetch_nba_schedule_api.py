"""Fetch NBA schedule data from the stats.nba.com LeagueGameLog endpoint.

Uses the ``nba_api`` package (project dependency) to call the official
stats.nba.com LeagueGameLog endpoint.  All network access is concentrated
here so callers and tests can mock it cleanly.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime

from nba_api.stats.endpoints.leaguegamelog import LeagueGameLog

from app.nba.normalize import TEAM_ABBREVIATIONS, canonical_team_name
from app.nba.seasons import get_official_nba_season, get_preseason_nba_season

logger = logging.getLogger(__name__)

LEAGUE_ID = "00"

SEASON_TYPES_API = [
    "Pre Season",
    "Regular Season",
    "PlayIn",
    "Playoffs",
]

_SEASON_TYPE_LABEL: dict[str, str] = {
    "Pre Season": "preseason",
    "Regular Season": "regular",
    "PlayIn": "play_in",
    "Playoffs": "playoffs",
}

_NBA_ABBREVIATIONS = frozenset(TEAM_ABBREVIATIONS.keys())

_MATCHUP_RE = re.compile(r"^(\w+)\s+(?:@|vs\.)\s+(\w+)$")

DEFAULT_TIMEOUT = 30
DEFAULT_RETRY_COUNT = 3
DEFAULT_RETRY_BACKOFF = 2.0


@dataclass(frozen=True)
class NormalizedGame:
    season: str
    game_date: str
    season_type: str
    away_team: str
    home_team: str
    matchup: str


def detect_current_season(today: date | None = None) -> str:
    """Return the NBA season string for *today*.

    NBA seasons span October-June:
    - Sep/Oct preseason -> upcoming season
    - Nov-Apr regular/play-in -> current season started previous Oct
    - May-Jun playoffs -> current season started previous Oct
    """
    d = today or date.today()
    if d.month >= 9:
        return f"{d.year}-{str(d.year + 1)[-2:]}"
    return f"{d.year - 1}-{str(d.year)[-2:]}"


def _parse_matchup(matchup: str) -> tuple[str, str] | None:
    """Return (away_abbr, home_abbr) from a MATCHUP string, or None."""
    m = _MATCHUP_RE.match(matchup.strip())
    if m is None:
        return None
    team_a, team_b = m.group(1), m.group(2)
    between = matchup.strip()[len(team_a):-len(team_b)]
    if "@" in between:
        return team_a, team_b
    return team_b, team_a


def _game_date_iso(raw: str) -> str:
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: {raw!r}")


def fetch_season_type(
    season: str,
    season_type_api: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_RETRY_COUNT,
    backoff: float = DEFAULT_RETRY_BACKOFF,
) -> list[dict]:
    """Fetch raw LeagueGameLog rows for one season/type from stats.nba.com.

    Returns a list of dicts with at least SEASON_ID, TEAM_ABBREVIATION,
    TEAM_NAME, GAME_ID, GAME_DATE, MATCHUP, WL.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            lg = LeagueGameLog(
                season=season,
                season_type_all_star=season_type_api,
                player_or_team_abbreviation="T",
                league_id=LEAGUE_ID,
                timeout=timeout,
            )
            df = lg.league_game_log.get_data_frame()
            if df.empty:
                logger.warning("No data for %s / %s", season, season_type_api)
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

        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Attempt %d/%d failed for %s/%s: %s",
                attempt,
                max_retries,
                season,
                season_type_api,
                exc,
            )
            if attempt < max_retries:
                time.sleep(backoff * attempt)

    raise RuntimeError(
        f"Failed to fetch {season}/{season_type_api} after {max_retries} attempts: "
        f"{last_exc}"
    )


def _deduplicate_rows(
    rows: list[dict],
    season: str,
    season_type_api: str,
) -> list[NormalizedGame]:
    """Collapse per-team rows into one NormalizedGame per GAME_ID.

    For each GAME_ID the NBA API returns exactly two rows – one per team –
    each with that team's own MATCHUP perspective (``"BOS @ NYK"`` from
    BOS's row vs. ``"NYK vs. BOS"`` from NYK's row).  We derive the two
    team abbreviations from the ``TEAM_ABBREVIATION`` fields of both rows,
    then parse either row's MATCHUP to determine home/away orientation.
    The MATCHUP-derived pair is cross-validated against the actual
    ``TEAM_ABBREVIATION`` values so that a mismatch (caused by unexpected
    API ordering or malformed data) is caught and skipped.
    """
    season_type = _SEASON_TYPE_LABEL[season_type_api]

    by_game: dict[str, list[dict]] = {}
    for row in rows:
        by_game.setdefault(str(row["GAME_ID"]), []).append(row)

    games: list[NormalizedGame] = []
    for game_id, team_rows in by_game.items():
        if len(team_rows) != 2:
            logger.debug(
                "Skipping game %s: expected 2 rows, got %d", game_id, len(team_rows)
            )
            continue

        row_abbrs = [r["TEAM_ABBREVIATION"] for r in team_rows]
        if not all(a in _NBA_ABBREVIATIONS for a in row_abbrs):
            logger.debug("Skipping game %s: non-NBA teams %s", game_id, row_abbrs)
            continue

        # Derive home/away from MATCHUP, trying both rows so that the
        # result is correct regardless of which team's row comes first.
        parsed: tuple[str, str] | None = None
        for r in team_rows:
            candidate = _parse_matchup(r["MATCHUP"])
            if candidate is not None:
                parsed = candidate
                break

        if parsed is None:
            logger.debug(
                "Skipping game %s: unparseable matchup in rows %s",
                game_id,
                [r["MATCHUP"] for r in team_rows],
            )
            continue

        away_abbr, home_abbr = parsed

        # Cross-validate: the two TEAM_ABBREVIATION values must match the
        # pair derived from MATCHUP.  A mismatch means the rows don't
        # actually belong to the same game.
        matchup_abbrs = sorted([away_abbr, home_abbr])
        if sorted(row_abbrs) != matchup_abbrs:
            logger.debug(
                "Skipping game %s: MATCHUP teams %s don't match "
                "TEAM_ABBREVIATIONs %s",
                game_id,
                matchup_abbrs,
                sorted(row_abbrs),
            )
            continue

        game_date = _game_date_iso(team_rows[0]["GAME_DATE"])

        away_name = canonical_team_name(TEAM_ABBREVIATIONS.get(away_abbr, away_abbr))
        home_name = canonical_team_name(TEAM_ABBREVIATIONS.get(home_abbr, home_abbr))

        if season_type == "preseason":
            resolved_season = get_preseason_nba_season(date.fromisoformat(game_date))
        else:
            resolved_season = get_official_nba_season(date.fromisoformat(game_date))

        games.append(
            NormalizedGame(
                season=resolved_season,
                game_date=game_date,
                season_type=season_type,
                away_team=away_name,
                home_team=home_name,
                matchup=f"{away_abbr}@{home_abbr}",
            )
        )

    games.sort(key=lambda g: (g.season, g.game_date, g.matchup))
    return games


def fetch_season_schedule(
    season: str,
    season_types: list[str] | None = None,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_RETRY_COUNT,
    backoff: float = DEFAULT_RETRY_BACKOFF,
    delay_between_requests: float = 1.0,
) -> list[NormalizedGame]:
    """Fetch and normalize the full schedule for a season.

    Iterates over each requested season type, fetches from the NBA API,
    deduplicates, and returns the merged list of NormalizedGame objects.
    """
    if season_types is None:
        season_types = list(SEASON_TYPES_API)

    all_games: list[NormalizedGame] = []
    first = True
    for st in season_types:
        if not first and delay_between_requests > 0:
            time.sleep(delay_between_requests)
        first = False

        logger.info("Fetching %s / %s …", season, st)
        raw = fetch_season_type(
            season,
            st,
            timeout=timeout,
            max_retries=max_retries,
            backoff=backoff,
        )
        games = _deduplicate_rows(raw, season, st)
        logger.info("Got %d games for %s / %s", len(games), season, st)
        all_games.extend(games)

    # Deduplicate across season types: the same physical game can appear
    # in multiple season-type queries (e.g. Pre Season and Regular Season)
    # with different GAME_IDs.  Collapse on (game_date, matchup) keeping
    # the last occurrence so that later season types (Regular > PreSeason,
    # Playoffs > PlayIn) take precedence.
    deduped_by_key: dict[tuple[str, str], NormalizedGame] = {}
    for g in all_games:
        key = (g.game_date, g.matchup)
        deduped_by_key[key] = g

    deduped = sorted(deduped_by_key.values(), key=lambda g: (g.season, g.game_date, g.matchup))
    return deduped


def normalized_games_to_rows(
    games: list[NormalizedGame],
) -> list[dict]:
    """Convert NormalizedGame objects to dicts suitable for upsert_schedule_rows."""
    return [
        {
            "season": g.season,
            "game_date": g.game_date,
            "season_type": g.season_type,
            "away_team": g.away_team,
            "home_team": g.home_team,
            "matchup": g.matchup,
        }
        for g in games
    ]
