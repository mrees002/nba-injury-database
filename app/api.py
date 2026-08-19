from __future__ import annotations

import csv
import io
from datetime import date, time
from pathlib import Path
from typing import Generator

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.session import build_engine, build_session_factory
from app.models.nba import (
    NBAPlayer,
    NBATeam,
    PublicInjuryEntry,
)

NBA_SEASONS: set[str] = {
    "2018-19", "2019-20", "2020-21", "2021-22",
    "2022-23", "2023-24", "2024-25", "2025-26",
}

ALLOWED_SEASON_TYPES = {"preseason", "regular", "play_in", "playoffs"}


def _resolve_seasons(values: list[str]) -> list[str]:
    """Validate and normalize season labels, returning cleaned strings."""
    result = []
    for v in values:
        key = v.strip()
        if key not in NBA_SEASONS:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported season '{v}'. "
                f"Valid seasons: {', '.join(sorted(NBA_SEASONS))}",
            )
        result.append(key)
    return result

_SEASON_TYPE_LABEL_MAP: dict[str, str] = {
    "Preseason": "preseason",
    "Regular Season": "regular",
    "Play-In": "play_in",
    "Playoffs": "playoffs",
}

_CSV_TO_SEASON_TYPE_LABEL: dict[str, str] = {v: k for k, v in _SEASON_TYPE_LABEL_MAP.items()}


def _normalize_season_type(value: str) -> str:
    label = value.strip()
    if label in _SEASON_TYPE_LABEL_MAP:
        return _SEASON_TYPE_LABEL_MAP[label]
    if label in ALLOWED_SEASON_TYPES:
        return label
    raise ValueError(
        f"Unsupported season type '{value}'. "
        f"Valid types: {', '.join(sorted(_SEASON_TYPE_LABEL_MAP.keys()))}"
    )


def _resolve_season_types(values: list[str]) -> list[str]:
    types = []
    for v in values:
        try:
            types.append(_normalize_season_type(v))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    return types


app = FastAPI(title="NBA Injury Database")

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

_engine = None
_session_factory = None


def _get_session_factory():
    global _engine, _session_factory
    if _session_factory is None:
        _engine = build_engine()
        _session_factory = build_session_factory(_engine)
    return _session_factory


def get_session() -> Generator[Session, None, None]:
    factory = _get_session_factory()
    with factory() as session:
        yield session


class EntryOut(BaseModel):
    id: int
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
    seasons: list[str] | None = None,
    season_types: list[str] | None = None,
):
    q = session.query(PublicInjuryEntry)
    if player_id is not None:
        q = q.filter(PublicInjuryEntry.player_id == player_id)
    if team_id is not None:
        q = q.filter(PublicInjuryEntry.team_id == team_id)
    if body_part is not None:
        q = q.filter(PublicInjuryEntry.body_part == body_part)
    if seasons is not None:
        q = q.filter(PublicInjuryEntry.season.in_(seasons))
    if season_types is not None:
        if not season_types:
            q = q.filter(PublicInjuryEntry.id == -1)
        else:
            q = q.filter(PublicInjuryEntry.season_type.in_(season_types))
    if start_date is not None:
        q = q.filter(PublicInjuryEntry.game_date >= start_date)
    if end_date is not None:
        q = q.filter(PublicInjuryEntry.game_date <= end_date)
    if statuses is not None:
        q = q.filter(PublicInjuryEntry.status.in_(statuses))
    if injury_type is not None:
        q = q.filter(PublicInjuryEntry.injury_type == injury_type)
    if reason_search is not None:
        q = q.filter(PublicInjuryEntry.raw_reason.ilike(f"%{reason_search}%"))
    q = q.order_by(
        PublicInjuryEntry.game_date.desc(),
        PublicInjuryEntry.matchup,
        PublicInjuryEntry.team_name,
        PublicInjuryEntry.player_name,
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
            game_date=entry.game_date,
            game_time=entry.game_time,
            matchup=entry.matchup,
            player_id=entry.player_id,
            player_name=entry.player_name,
            team_id=entry.team_id,
            team_name=entry.team_name,
            status=entry.status,
            raw_reason=entry.raw_reason,
            reason_category=entry.reason_category,
            body_part=entry.body_part,
            injury_type=entry.injury_type,
            source_url=entry.source_url,
        )
        for entry in rows
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
    for entry in rows:
        writer.writerow(
            [
                entry.id,
                entry.player_name,
                entry.team_name,
                entry.season,
                _CSV_TO_SEASON_TYPE_LABEL.get(entry.season_type, entry.season_type) if entry.season_type else None,
                entry.game_date,
                entry.matchup,
                entry.status,
                entry.raw_reason,
                entry.reason_category,
                entry.body_part,
                entry.injury_type,
                entry.source_url,
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
