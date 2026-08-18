from __future__ import annotations

import csv
import io
from datetime import date, time
from pathlib import Path
from typing import Generator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import and_, exists, or_, select, table, column, text as sa_text, tuple_
from sqlalchemy.orm import Session

from app.db.session import build_engine, build_session_factory
from app.models.nba import NBAInjuryCondition, NBAPlayer, NBAReport, NBAReportEntry, NBATeam

# Explicit season mapping: label -> (inclusive start_date, inclusive end_date).
# Avoids generic July-1 rules; each boundary is set to actual NBA game dates.
NBA_SEASONS: dict[str, tuple[date, date]] = {
    "2018-19": (date(2018, 10, 16), date(2019, 6, 13)),
    "2019-20": (date(2019, 10, 22), date(2020, 10, 11)),  # COVID bubble
    "2020-21": (date(2020, 12, 22), date(2021, 7, 20)),   # delayed start, July finals
    "2021-22": (date(2021, 10, 19), date(2022, 6, 26)),
    "2022-23": (date(2022, 10, 18), date(2023, 6, 20)),
    "2023-24": (date(2023, 10, 24), date(2024, 6, 23)),
    "2024-25": (date(2024, 10, 22), date(2025, 6, 22)),
    "2025-26": (date(2025, 10, 21), date(2026, 6, 13)),
}

SCHEDULE_CSV = Path(__file__).resolve().parent.parent / "data" / "reference" / "nba_schedule_games.csv"

ALLOWED_SEASON_TYPES = {"preseason", "regular", "play_in", "playoffs"}

# Map frontend labels -> CSV season_type values.
_SEASON_TYPE_LABEL_MAP: dict[str, str] = {
    "Preseason": "preseason",
    "Regular Season": "regular",
    "Play-In": "play_in",
    "Playoffs": "playoffs",
}


def _load_schedule() -> dict[tuple[str, str], str]:
    """Load schedule CSV and build lookup: (game_date_str, normalized_matchup) -> season_type."""
    lookup: dict[tuple[str, str], str] = {}
    with open(SCHEDULE_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row["game_date"], row["matchup"].replace(" ", ""))
            lookup[key] = row["season_type"]
    return lookup


_SCHEDULE_LOOKUP: dict[tuple[str, str], str] = _load_schedule()

# Reverse map: CSV season_type value -> display label.
_CSV_TO_SEASON_TYPE_LABEL: dict[str, str] = {v: k for k, v in _SEASON_TYPE_LABEL_MAP.items()}


def _derive_season(game_date: date) -> str | None:
    """Return the NBA season label for a given game date, or None if unmatched."""
    for label, (start, end) in NBA_SEASONS.items():
        if start <= game_date <= end:
            return label
    return None


def _derive_season_type(game_date: date, matchup: str) -> str | None:
    """Return the display season type label for a (game_date, matchup) pair."""
    key = (game_date.isoformat(), matchup.replace(" ", ""))
    csv_val = _SCHEDULE_LOOKUP.get(key)
    if csv_val is None:
        return None
    return _CSV_TO_SEASON_TYPE_LABEL.get(csv_val)


def _resolve_season(value: str) -> tuple[date, date]:
    """Return (start, end) for a season label or raise ValueError."""
    key = value.strip()
    if key not in NBA_SEASONS:
        raise ValueError(
            f"Unsupported season '{value}'. "
            f"Valid seasons: {', '.join(sorted(NBA_SEASONS))}"
        )
    return NBA_SEASONS[key]


def _resolve_seasons(values: list[str]) -> list[tuple[date, date]]:
    """Resolve multiple season labels to a list of (start, end) ranges."""
    ranges = []
    for v in values:
        try:
            ranges.append(_resolve_season(v))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    return ranges


def _normalize_season_type(value: str) -> str:
    """Map a frontend season type label to its CSV value, or raise ValueError."""
    label = value.strip()
    if label in _SEASON_TYPE_LABEL_MAP:
        return _SEASON_TYPE_LABEL_MAP[label]
    # Also accept raw CSV values directly.
    if label in ALLOWED_SEASON_TYPES:
        return label
    raise ValueError(
        f"Unsupported season type '{value}'. "
        f"Valid types: {', '.join(sorted(_SEASON_TYPE_LABEL_MAP.keys()))}"
    )


def _resolve_season_types(values: list[str]) -> list[str]:
    """Resolve multiple season type labels to their CSV values."""
    types = []
    for v in values:
        try:
            types.append(_normalize_season_type(v))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    return types

app = FastAPI(title="NBA Injury Database")

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

_engine = build_engine()
_session_factory = build_session_factory(_engine)


def get_session() -> Generator[Session, None, None]:
    with _session_factory() as session:
        yield session


class EntryOut(BaseModel):
    id: int
    report_id: int
    game_date: date
    game_time: time | None
    matchup: str
    player_id: int | None
    player_name: str | None
    team_id: int | None
    team_name: str | None
    status: str | None
    raw_reason: str | None
    reason_category: str | None
    body_part: str | None
    injury_type: str | None
    previous_status: str | None
    previous_reason: str | None
    source_url: str | None

    model_config = {"from_attributes": True}


_CSV_COLUMNS = [
    "id",
    "player_name",
    "team_name",
    "season",
    "season_type",
    "game_date",
    "matchup",
    "status",
    "raw_reason",
    "reason_category",
    "body_part",
    "injury_type",
    "source_url",
]

_CONDITION_SQ = (
    select(
        NBAInjuryCondition.report_entry_id,
        NBAInjuryCondition.body_part,
        NBAInjuryCondition.injury_type,
    )
    .where(NBAInjuryCondition.condition_index == 1)
    .subquery()
)


def _build_season_type_subquery(session: Session, season_types: list[str]):
    """Create a temporary table with qualifying schedule pairs and return a subquery.

    Instead of expanding thousands of (game_date, matchup) pairs into a tuple IN clause,
    we load qualifying pairs from the schedule CSV into a temporary table and filter
    via an EXISTS subquery.  This avoids both the expensive DB round-trip that fetches
    all distinct pairs and the giant bound-parameter IN clause.
    """
    qualifying: set[tuple[str, str]] = set()
    for (gd_str, mu_norm), csv_type in _SCHEDULE_LOOKUP.items():
        if csv_type in season_types:
            qualifying.add((gd_str, mu_norm))

    if not qualifying:
        return None

    conn = session.connection()
    conn.execute(sa_text(
        "CREATE TEMPORARY TABLE IF NOT EXISTS _season_type_filter "
        "(game_date DATE NOT NULL, matchup TEXT NOT NULL)"
    ))
    conn.execute(sa_text("DELETE FROM _season_type_filter"))

    rows = [{"gd": gd, "mu": mu} for gd, mu in qualifying]
    batch_size = 500
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        conn.execute(
            sa_text("INSERT INTO _season_type_filter (game_date, matchup) VALUES (:gd, :mu)"),
            batch,
        )

    sched_tbl = table(
        "_season_type_filter",
        column("game_date"),
        column("matchup"),
    )
    return select(sched_tbl).subquery("_stf")


def _build_entry_query(
    session: Session,
    player_id: int | None = None,
    team_id: int | None = None,
    body_part: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    statuses: list[str] | None = None,
    injury_type: str | None = None,
    reason_search: str | None = None,
    seasons: list[tuple[date, date]] | None = None,
    season_types: list[str] | None = None,
):
    q = (
        session.query(
            NBAReportEntry,
            NBAPlayer.canonical_name,
            NBATeam.canonical_name,
            _CONDITION_SQ.c.body_part,
            _CONDITION_SQ.c.injury_type,
            NBAReport.source_url,
        )
        .join(NBAPlayer, NBAReportEntry.player_id == NBAPlayer.id)
        .outerjoin(NBATeam, NBAReportEntry.team_id == NBATeam.id)
        .outerjoin(NBAReport, NBAReportEntry.report_id == NBAReport.id)
        .outerjoin(_CONDITION_SQ, _CONDITION_SQ.c.report_entry_id == NBAReportEntry.id)
    )
    if player_id is not None:
        q = q.filter(NBAReportEntry.player_id == player_id)
    if team_id is not None:
        q = q.filter(NBAReportEntry.team_id == team_id)
    if body_part is not None:
        q = q.filter(
            exists()
            .where(
                NBAInjuryCondition.report_entry_id == NBAReportEntry.id,
                NBAInjuryCondition.body_part == body_part,
            )
            .correlate(NBAReportEntry)
        )
    if seasons is not None:
        season_conditions = []
        for s_start, s_end in seasons:
            season_conditions.append(
                and_(
                    NBAReportEntry.game_date >= s_start,
                    NBAReportEntry.game_date <= s_end,
                )
            )
        q = q.filter(or_(*season_conditions))
    if season_types is not None:
        st_subq = _build_season_type_subquery(session, season_types)
        if st_subq is None:
            q = q.filter(NBAReportEntry.id == -1)
        else:
            q = q.filter(
                exists()
                .where(
                    st_subq.c.game_date == NBAReportEntry.game_date,
                    st_subq.c.matchup == NBAReportEntry.matchup,
                )
            )
    if start_date is not None:
        q = q.filter(NBAReportEntry.game_date >= start_date)
    if end_date is not None:
        q = q.filter(NBAReportEntry.game_date <= end_date)
    if statuses is not None:
        q = q.filter(NBAReportEntry.status.in_(statuses))
    if injury_type is not None:
        q = q.filter(
            exists()
            .where(
                NBAInjuryCondition.report_entry_id == NBAReportEntry.id,
                NBAInjuryCondition.injury_type == injury_type,
            )
            .correlate(NBAReportEntry)
        )
    if reason_search is not None:
        q = q.filter(NBAReportEntry.raw_reason.ilike(f"%{reason_search}%"))
    q = q.order_by(
        NBAReportEntry.game_date.desc(),
        NBAReportEntry.matchup,
        NBATeam.canonical_name,
        NBAPlayer.canonical_name,
    )
    return q


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_TEMPLATE_DIR / "index.html")


@app.get("/injuries", response_model=list[EntryOut])
def list_injuries(
    session: Session = Depends(get_session),
    player_id: int | None = Query(default=None),
    team_id: int | None = Query(default=None),
    body_part: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    injury_type: str | None = Query(default=None),
    reason_search: str | None = Query(default=None),
    season: list[str] | None = Query(default=None),
    season_type: list[str] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> list[EntryOut]:
    resolved_seasons = _resolve_seasons(season) if season else None
    resolved_season_types = _resolve_season_types(season_type) if season_type else None
    q = _build_entry_query(
        session, player_id, team_id, body_part, start_date, end_date, status,
        injury_type, reason_search, resolved_seasons, resolved_season_types,
    )
    rows = q.offset((page - 1) * page_size).limit(page_size).all()

    return [
        EntryOut(
            id=entry.id,
            report_id=entry.report_id,
            game_date=entry.game_date,
            game_time=entry.game_time,
            matchup=entry.matchup,
            player_id=entry.player_id,
            player_name=p_name,
            team_id=entry.team_id,
            team_name=t_name,
            status=entry.status,
            raw_reason=entry.raw_reason,
            reason_category=entry.reason_category,
            body_part=bp,
            injury_type=it,
            previous_status=entry.previous_status,
            previous_reason=entry.previous_reason,
            source_url=src_url,
        )
        for entry, p_name, t_name, bp, it, src_url in rows
    ]


@app.get("/injuries.csv")
def list_injuries_csv(
    session: Session = Depends(get_session),
    player_id: int | None = Query(default=None),
    team_id: int | None = Query(default=None),
    body_part: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    injury_type: str | None = Query(default=None),
    reason_search: str | None = Query(default=None),
    season: list[str] | None = Query(default=None),
    season_type: list[str] | None = Query(default=None),
) -> StreamingResponse:
    resolved_seasons = _resolve_seasons(season) if season else None
    resolved_season_types = _resolve_season_types(season_type) if season_type else None
    q = _build_entry_query(
        session, player_id, team_id, body_part, start_date, end_date, status,
        injury_type, reason_search, resolved_seasons, resolved_season_types,
    )
    rows = q.all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_COLUMNS)
    for entry, p_name, t_name, bp, it, src_url in rows:
        writer.writerow(
            [
                entry.id,
                p_name,
                t_name,
                _derive_season(entry.game_date),
                _derive_season_type(entry.game_date, entry.matchup),
                entry.game_date,
                entry.matchup,
                entry.status,
                entry.raw_reason,
                entry.reason_category,
                bp,
                it,
                src_url,
            ]
        )
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=injuries.csv"},
    )


class PlayerOut(BaseModel):
    id: int
    canonical_name: str

    model_config = {"from_attributes": True}


class TeamOut(BaseModel):
    id: int
    canonical_name: str

    model_config = {"from_attributes": True}


@app.get("/players", response_model=list[PlayerOut])
def list_players(session: Session = Depends(get_session)) -> list[PlayerOut]:
    return session.query(NBAPlayer).all()


@app.get("/teams", response_model=list[TeamOut])
def list_teams(session: Session = Depends(get_session)) -> list[TeamOut]:
    return session.query(NBATeam).all()


@app.get("/api/players/{player_id}", response_model=PlayerOut)
def get_player(player_id: int, session: Session = Depends(get_session)) -> PlayerOut:
    player = session.query(NBAPlayer).filter(NBAPlayer.id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


@app.get("/players/{player_id}")
def player_detail(player_id: int) -> FileResponse:
    return FileResponse(_TEMPLATE_DIR / "player_detail.html")
