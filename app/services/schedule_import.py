from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.nba import NBAScheduleGame

REQUIRED_COLUMNS = ("season", "game_date", "season_type", "away_team", "home_team", "matchup")
LOOKUP_BATCH_SIZE = 500


class ScheduleCSVValidationError(ValueError):
    """Raised when a CSV cannot be interpreted as a schedule file."""


@dataclass(frozen=True)
class ScheduleImportResult:
    read: int
    inserted: int
    skipped: int
    invalid: int


@dataclass(frozen=True)
class ParsedScheduleGame:
    season: str
    game_date: date
    season_type: str
    away_team: str
    home_team: str
    matchup: str


def _read_schedule_csv(
    path: Path,
) -> tuple[list[ParsedScheduleGame], int, int]:
    games: list[ParsedScheduleGame] = []
    rows_read = 0
    invalid_rows = 0

    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file, strict=True)
        if reader.fieldnames is None:
            raise ScheduleCSVValidationError("CSV is empty or does not contain a header row")

        missing_columns = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
        if missing_columns:
            missing = ", ".join(missing_columns)
            raise ScheduleCSVValidationError(f"CSV is missing required columns: {missing}")

        try:
            for row in reader:
                rows_read += 1
                if None in row or any(row.get(c) is None for c in REQUIRED_COLUMNS):
                    invalid_rows += 1
                    continue

                try:
                    game_date = date.fromisoformat(row["game_date"].strip())
                except (ValueError, AttributeError):
                    invalid_rows += 1
                    continue

                games.append(
                    ParsedScheduleGame(
                        season=row["season"].strip(),
                        game_date=game_date,
                        season_type=row["season_type"].strip(),
                        away_team=row["away_team"].strip(),
                        home_team=row["home_team"].strip(),
                        matchup=row["matchup"].strip(),
                    )
                )
        except csv.Error as exc:
            raise ScheduleCSVValidationError(
                f"malformed CSV near line {reader.line_num}: {exc}"
            ) from exc

    return games, rows_read, invalid_rows


def _batched(values: list, batch_size: int) -> Iterable[list]:
    for start in range(0, len(values), batch_size):
        yield values[start : start + batch_size]


def _lookup_existing_keys(
    session: Session, games: list[ParsedScheduleGame]
) -> set[tuple[str, date, str]]:
    """Return set of (season, game_date, matchup) tuples already in the DB."""
    keys = list(dict.fromkeys((g.season, g.game_date, g.matchup) for g in games))
    existing: set[tuple[str, date, str]] = set()
    for key_batch in _batched(keys, LOOKUP_BATCH_SIZE):
        rows = session.execute(
            select(
                NBAScheduleGame.season,
                NBAScheduleGame.game_date,
                NBAScheduleGame.matchup,
            ).where(
                NBAScheduleGame.season.in_([k[0] for k in key_batch]),
                NBAScheduleGame.game_date.in_([k[1] for k in key_batch]),
                NBAScheduleGame.matchup.in_([k[2] for k in key_batch]),
            )
        ).all()
        existing.update(rows)
    return existing


def import_schedule_csv(
    session: Session,
    path: str | Path,
    source: str | None = None,
) -> ScheduleImportResult:
    """Import schedule CSV idempotently. Existing rows are skipped."""
    csv_path = Path(path)
    parsed, rows_read, invalid_rows = _read_schedule_csv(csv_path)

    existing_keys = _lookup_existing_keys(session, parsed)

    new_games: list[NBAScheduleGame] = []
    seen_keys: set[tuple[str, date, str]] = set()
    skipped = 0

    for game in parsed:
        key = (game.season, game.game_date, game.matchup)
        if key in existing_keys or key in seen_keys:
            skipped += 1
            continue

        seen_keys.add(key)
        new_games.append(
            NBAScheduleGame(
                season=game.season,
                game_date=game.game_date,
                season_type=game.season_type,
                away_team=game.away_team,
                home_team=game.home_team,
                matchup=game.matchup,
                source=source,
            )
        )

    try:
        session.add_all(new_games)
        session.commit()
    except Exception:
        session.rollback()
        raise

    return ScheduleImportResult(
        read=rows_read,
        inserted=len(new_games),
        skipped=skipped,
        invalid=invalid_rows,
    )
