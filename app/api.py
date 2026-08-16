from __future__ import annotations

import csv
import io
from datetime import date, time
from pathlib import Path
from typing import Generator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import exists, select
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


def _resolve_season(value: str) -> tuple[date, date]:
    """Return (start, end) for a season label or raise ValueError."""
    key = value.strip()
    if key not in NBA_SEASONS:
        raise ValueError(
            f"Unsupported season '{value}'. "
            f"Valid seasons: {', '.join(sorted(NBA_SEASONS))}"
        )
    return NBA_SEASONS[key]

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
    "report_id",
    "game_date",
    "game_time",
    "matchup",
    "player_id",
    "player_name",
    "team_id",
    "team_name",
    "status",
    "raw_reason",
    "reason_category",
    "body_part",
    "injury_type",
    "previous_status",
    "previous_reason",
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
    season: tuple[date, date] | None = None,
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
    if season is not None:
        season_start, season_end = season
        q = q.filter(NBAReportEntry.game_date >= season_start)
        q = q.filter(NBAReportEntry.game_date <= season_end)
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
        NBAReportEntry.game_date,
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
    season: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> list[EntryOut]:
    season_range = None
    if season is not None:
        try:
            season_range = _resolve_season(season)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    q = _build_entry_query(
        session, player_id, team_id, body_part, start_date, end_date, status,
        injury_type, reason_search, season_range
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
    season: str | None = Query(default=None),
) -> StreamingResponse:
    season_range = None
    if season is not None:
        try:
            season_range = _resolve_season(season)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    q = _build_entry_query(
        session, player_id, team_id, body_part, start_date, end_date, status,
        injury_type, reason_search, season_range
    )
    rows = q.all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_COLUMNS)
    for entry, p_name, t_name, bp, it, src_url in rows:
        writer.writerow(
            [
                entry.id,
                entry.report_id,
                entry.game_date,
                entry.game_time,
                entry.matchup,
                entry.player_id,
                p_name,
                entry.team_id,
                t_name,
                entry.status,
                entry.raw_reason,
                entry.reason_category,
                bp,
                it,
                entry.previous_status,
                entry.previous_reason,
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
