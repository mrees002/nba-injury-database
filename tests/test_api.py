from __future__ import annotations

import csv
import io
from datetime import date, time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api import app, get_session
from app.db.base import Base
from app.models.nba import (
    NBAPlayer,
    NBATeam,
    PublicInjuryEntry,
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient

    def _override():
        yield db

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed(session: Session, *, entry_count: int = 1, condition_counts: dict | None = None):
    """Insert PublicInjuryEntry rows directly.

    Returns list of entry_ids.
    """
    player = NBAPlayer(id=100, canonical_name="Test Player", name_key="test player")
    team = NBATeam(id=200, canonical_name="Test Team", abbreviation="TST")
    session.add_all([player, team])
    session.flush()

    entries = []
    for i in range(1, entry_count + 1):
        entry = PublicInjuryEntry(
            id=500 + i,
            source_url="https://example.com/report.pdf",
            source_report_date=date(2025, 1, 15),
            source_report_time=time(17, 0),
            row_number=i,
            game_date=date(2025, 1, 15),
            game_time=time(19, 30),
            matchup="TST @ OPP",
            player_id=100,
            player_name="Test Player",
            team_id=200,
            team_name="Test Team",
            status="Out",
            reason_category="Injury",
            raw_reason="sore left knee",
            body_part="Knee",
            injury_type="Soreness",
            season="2024-25",
            season_type="regular",
        )
        session.add(entry)
        session.flush()
        entries.append(entry.id)
    session.commit()
    return entries


# ── JSON basics ──────────────────────────────────────────────────────────────

def test_json_returns_entry_fields(client, db):
    _seed(db)
    resp = client.get("/injuries")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert set(row) == {
        "id",
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
        "source_url",
    }
    assert row["player_name"] == "Test Player"
    assert row["team_name"] == "Test Team"
    assert row["body_part"] == "Knee"
    assert row["injury_type"] == "Soreness"


def test_json_empty_database(client):
    resp = client.get("/injuries")
    assert resp.status_code == 200
    assert resp.json() == []


# ── CSV basics ───────────────────────────────────────────────────────────────

def test_csv_returns_correct_columns_and_data(client, db):
    _seed(db)
    resp = client.get("/injuries.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert "attachment" in resp.headers.get("content-disposition", "")
    text = resp.text
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    assert header == [
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
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0][1] == "Test Player"  # player_name
    assert rows[0][10] == "Knee"  # body_part


def test_csv_no_pagination(client, db):
    _seed(db, entry_count=3)
    resp = client.get("/injuries.csv")
    reader = csv.reader(io.StringIO(resp.text))
    next(reader)  # skip header
    rows = list(reader)
    assert len(rows) == 3


# ── Pagination ───────────────────────────────────────────────────────────────

def test_pagination_default_page_size(client, db):
    _seed(db, entry_count=5)
    resp = client.get("/injuries")
    assert len(resp.json()) == 5


def test_pagination_second_page(client, db):
    _seed(db, entry_count=5)
    resp = client.get("/injuries?page=2&page_size=2")
    data = resp.json()
    assert len(data) == 2
    all_ids = {d["id"] for d in client.get("/injuries?page_size=5").json()}
    assert {d["id"] for d in data} < all_ids


def test_pagination_beyond_last_page(client, db):
    _seed(db, entry_count=3)
    resp = client.get("/injuries?page=10&page_size=10")
    assert resp.json() == []


# ── Filters ──────────────────────────────────────────────────────────────────

def test_filter_by_player_id(client, db):
    _seed(db)
    entry2 = PublicInjuryEntry(
        id=510,
        source_url="https://example.com/report2.pdf",
        source_report_date=date(2025, 1, 16),
        source_report_time=time(17, 0),
        row_number=10,
        game_date=date(2025, 1, 16),
        matchup="TST @ OPP",
        player_id=101,
        player_name="Other Player",
        team_id=200,
        team_name="Test Team",
        status="Probable",
        raw_reason="back tightness",
        body_part="Back",
        injury_type="Tightness",
        season="2024-25",
        season_type="regular",
    )
    db.add(entry2)
    db.commit()

    resp = client.get("/injuries?player_id=101")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["player_id"] == 101


def test_filter_by_team_id(client, db):
    _seed(db)
    entry2 = PublicInjuryEntry(
        id=511,
        source_url="https://example.com/report3.pdf",
        source_report_date=date(2025, 1, 16),
        source_report_time=time(17, 0),
        row_number=11,
        game_date=date(2025, 1, 16),
        matchup="OTH @ OPP",
        player_id=100,
        player_name="Test Player",
        team_id=201,
        team_name="Other Team",
        status="Doubtful",
        raw_reason="ankle pain",
        body_part="Ankle",
        injury_type="Pain",
        season="2024-25",
        season_type="regular",
    )
    db.add(entry2)
    db.commit()

    resp = client.get("/injuries?team_id=201")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["team_id"] == 201


def test_filter_by_body_part(client, db):
    _seed(db)
    entry2 = PublicInjuryEntry(
        id=512,
        source_url="https://example.com/report4.pdf",
        source_report_date=date(2025, 1, 15),
        source_report_time=time(17, 0),
        row_number=12,
        game_date=date(2025, 1, 15),
        matchup="TST @ OPP",
        player_id=100,
        player_name="Test Player",
        team_id=200,
        team_name="Test Team",
        status="Out",
        raw_reason="ankle sprain",
        body_part="Ankle",
        injury_type="Sprain",
        season="2024-25",
        season_type="regular",
    )
    db.add(entry2)
    db.commit()

    resp_knee = client.get("/injuries?body_part=Knee")
    assert len(resp_knee.json()) == 1

    resp_ankle = client.get("/injuries?body_part=Ankle")
    assert len(resp_ankle.json()) == 1

    resp_back = client.get("/injuries?body_part=Back")
    assert len(resp_back.json()) == 0


def test_filter_combined(client, db):
    _seed(db)
    entry2 = PublicInjuryEntry(
        id=513,
        source_url="https://example.com/report5.pdf",
        source_report_date=date(2025, 1, 16),
        source_report_time=time(17, 0),
        row_number=13,
        game_date=date(2025, 1, 16),
        matchup="TST @ OPP",
        player_id=101,
        player_name="Other Player",
        team_id=200,
        team_name="Test Team",
        status="Out",
        raw_reason="ankle sprain",
        body_part="Ankle",
        injury_type="Sprain",
        season="2024-25",
        season_type="regular",
    )
    db.add(entry2)
    db.commit()

    resp = client.get("/injuries?player_id=100&body_part=Knee")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["player_id"] == 100


# ── start_date / end_date / status filters ─────────────────────────────────────

def _seed_multi_date(session: Session):
    """Seed three entries on different dates and statuses."""
    player = NBAPlayer(id=100, canonical_name="Test Player", name_key="test player")
    team = NBATeam(id=200, canonical_name="Test Team", abbreviation="TST")
    session.add_all([player, team])
    session.flush()

    specs = [
        (501, date(2025, 1, 10), "Out"),
        (502, date(2025, 1, 15), "Questionable"),
        (503, date(2025, 1, 20), "Out"),
    ]
    for eid, gd, st in specs:
        entry = PublicInjuryEntry(
            id=eid,
            source_url="https://example.com/report.pdf",
            source_report_date=gd,
            source_report_time=time(17, 0),
            row_number=eid - 500,
            game_date=gd,
            game_time=time(19, 30),
            matchup="TST @ OPP",
            player_id=100,
            player_name="Test Player",
            team_id=200,
            team_name="Test Team",
            status=st,
            reason_category="Injury",
            raw_reason="sore knee",
            body_part="Knee",
            injury_type="Soreness",
            season="2024-25",
            season_type="regular",
        )
        session.add(entry)
    session.commit()


def test_start_date_filter(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries?start_date=2025-01-15")
    data = resp.json()
    assert len(data) == 2
    dates = [r["game_date"] for r in data]
    assert all(d >= "2025-01-15" for d in dates)


def test_end_date_filter(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries?end_date=2025-01-15")
    data = resp.json()
    assert len(data) == 2
    dates = [r["game_date"] for r in data]
    assert all(d <= "2025-01-15" for d in dates)


def test_date_range_filter(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries?start_date=2025-01-12&end_date=2025-01-18")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["game_date"] == "2025-01-15"


def test_start_date_exclusive(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries?start_date=2025-01-16")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["game_date"] == "2025-01-20"


def test_end_date_exclusive(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries?end_date=2025-01-14")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["game_date"] == "2025-01-10"


def test_date_range_no_match(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries?start_date=2025-02-01&end_date=2025-02-28")
    assert resp.json() == []


def test_status_filter(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries?status=Out")
    data = resp.json()
    assert len(data) == 2
    assert all(r["status"] == "Out" for r in data)


def test_status_filter_no_match(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries?status=Outlandish")
    assert resp.json() == []


def test_status_exact_match(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries?status=Questionable")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "Questionable"


def test_status_filter_case_sensitive(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries?status=out")
    assert resp.json() == []


def test_combined_date_and_status(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries?start_date=2025-01-10&end_date=2025-01-14&status=Out")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["game_date"] == "2025-01-10"
    assert data[0]["status"] == "Out"


def test_combined_all_filters(client, db):
    _seed_multi_date(db)
    resp = client.get(
        "/injuries?start_date=2025-01-01&end_date=2025-12-31"
        "&status=Questionable&player_id=100&team_id=200"
    )
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "Questionable"
    assert data[0]["player_id"] == 100
    assert data[0]["team_id"] == 200


# ── CSV / JSON parity ─────────────────────────────────────────────────────────

def _csv_ids(client, url: str) -> list[int]:
    resp = client.get(url)
    reader = csv.reader(io.StringIO(resp.text))
    next(reader)
    return [int(row[0]) for row in reader]


def test_csv_start_date_parity(client, db):
    _seed_multi_date(db)
    j = client.get("/injuries?start_date=2025-01-15").json()
    c = _csv_ids(client, "/injuries.csv?start_date=2025-01-15")
    assert [r["id"] for r in j] == c


def test_csv_end_date_parity(client, db):
    _seed_multi_date(db)
    j = client.get("/injuries?end_date=2025-01-15").json()
    c = _csv_ids(client, "/injuries.csv?end_date=2025-01-15")
    assert [r["id"] for r in j] == c


def test_csv_date_range_parity(client, db):
    _seed_multi_date(db)
    j = client.get("/injuries?start_date=2025-01-12&end_date=2025-01-18").json()
    c = _csv_ids(client, "/injuries.csv?start_date=2025-01-12&end_date=2025-01-18")
    assert [r["id"] for r in j] == c


def test_csv_status_parity(client, db):
    _seed_multi_date(db)
    j = client.get("/injuries?status=Out").json()
    c = _csv_ids(client, "/injuries.csv?status=Out")
    assert [r["id"] for r in j] == c


def test_csv_combined_filters_parity(client, db):
    _seed_multi_date(db)
    j = client.get(
        "/injuries?start_date=2025-01-10&end_date=2025-01-20&status=Out"
    ).json()
    c = _csv_ids(
        client,
        "/injuries.csv?start_date=2025-01-10&end_date=2025-01-20&status=Out",
    )
    assert [r["id"] for r in j] == c


# ── injury_type filter ────────────────────────────────────────────────────────

def test_injury_type_filter(client, db):
    _seed(db)
    resp = client.get("/injuries?injury_type=Soreness")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["injury_type"] == "Soreness"


def test_injury_type_filter_no_match(client, db):
    _seed(db)
    resp = client.get("/injuries?injury_type=Fracture")
    assert resp.json() == []


def test_injury_type_filter_case_sensitive(client, db):
    _seed(db)
    resp = client.get("/injuries?injury_type=soreness")
    assert resp.json() == []


# ── reason_search filter ──────────────────────────────────────────────────────

def test_reason_search_filter(client, db):
    _seed(db)
    resp = client.get("/injuries?reason_search=knee")
    data = resp.json()
    assert len(data) == 1
    assert "knee" in data[0]["raw_reason"].lower()


def test_reason_search_case_insensitive(client, db):
    _seed(db)
    resp = client.get("/injuries?reason_search=KNEE")
    data = resp.json()
    assert len(data) == 1


def test_reason_search_partial_match(client, db):
    _seed(db)
    resp = client.get("/injuries?reason_search=kne")
    data = resp.json()
    assert len(data) == 1


def test_reason_search_no_match(client, db):
    _seed(db)
    resp = client.get("/injuries?reason_search=shoulder")
    assert resp.json() == []


# ── combined injury_type + reason_search ──────────────────────────────────────

def test_combined_injury_type_and_reason_search(client, db):
    _seed(db)
    resp = client.get("/injuries?injury_type=Soreness&reason_search=knee")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["injury_type"] == "Soreness"
    assert "knee" in data[0]["raw_reason"].lower()


def test_combined_injury_type_and_reason_search_no_match(client, db):
    _seed(db)
    resp = client.get("/injuries?injury_type=Soreness&reason_search=ankle")
    assert resp.json() == []


def test_combined_all_new_filters_with_existing(client, db):
    _seed(db)
    resp = client.get(
        "/injuries?injury_type=Soreness&reason_search=knee&team_id=200"
    )
    data = resp.json()
    assert len(data) == 1
    assert data[0]["injury_type"] == "Soreness"


# ── Multi-value status filter ────────────────────────────────────────────────

def test_status_one_value(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries?status=Out")
    data = resp.json()
    assert len(data) == 2
    assert all(r["status"] == "Out" for r in data)


def test_status_multiple_values(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries?status=Out&status=Questionable")
    data = resp.json()
    assert len(data) == 3
    statuses = {r["status"] for r in data}
    assert statuses == {"Out", "Questionable"}


def test_status_no_filter_returns_all(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries")
    data = resp.json()
    assert len(data) == 3


def test_status_no_match(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries?status=Outlandish")
    assert resp.json() == []


def test_status_multiple_no_match(client, db):
    _seed_multi_date(db)
    resp = client.get("/injuries?status=Outlandish&status=AlsoFake")
    assert resp.json() == []


def test_status_csv_one_value_parity(client, db):
    _seed_multi_date(db)
    j = client.get("/injuries?status=Out").json()
    c = _csv_ids(client, "/injuries.csv?status=Out")
    assert [r["id"] for r in j] == c


def test_status_csv_multiple_values_parity(client, db):
    _seed_multi_date(db)
    j = client.get("/injuries?status=Out&status=Questionable").json()
    c = _csv_ids(client, "/injuries.csv?status=Out&status=Questionable")
    assert [r["id"] for r in j] == c


# ── Default ordering ──────────────────────────────────────────────────────────


def _seed_ordering(session: Session):
    """Seed entries to verify deterministic default ordering.

    Layout (game_date, matchup, team, player):
      2025-01-10, LAL @ BOS, Boston Celtics,   "Bravo, Alpha"
      2025-01-10, LAL @ BOS, Los Angeles Lakers, "Charlie"
      2025-01-10, MIL @ GSW, Golden State Warriors, "Delta"
      2025-01-10, MIL @ GSW, Milwaukee Bucks,      "Echo"
      2025-01-15, LAL @ BOS, Boston Celtics,      "Foxtrot"
      2025-01-20, LAL @ BOS, Boston Celtics,      "Golf"
    """
    players = [
        NBAPlayer(id=101, canonical_name="Alpha", name_key="alpha"),
        NBAPlayer(id=102, canonical_name="Bravo", name_key="bravo"),
        NBAPlayer(id=103, canonical_name="Charlie", name_key="charlie"),
        NBAPlayer(id=104, canonical_name="Delta", name_key="delta"),
        NBAPlayer(id=105, canonical_name="Echo", name_key="echo"),
        NBAPlayer(id=106, canonical_name="Foxtrot", name_key="foxtrot"),
        NBAPlayer(id=107, canonical_name="Golf", name_key="golf"),
    ]
    teams = [
        NBATeam(id=201, canonical_name="Boston Celtics", abbreviation="BOS"),
        NBATeam(id=202, canonical_name="Los Angeles Lakers", abbreviation="LAL"),
        NBATeam(id=203, canonical_name="Golden State Warriors", abbreviation="GSW"),
        NBATeam(id=204, canonical_name="Milwaukee Bucks", abbreviation="MIL"),
    ]
    session.add_all(players + teams)
    session.flush()

    # (id, game_date, matchup, team_id, player_id, team_name, player_name)
    specs = [
        (501, date(2025, 1, 10), "LAL @ BOS", 201, 102, "Boston Celtics", "Bravo"),
        (502, date(2025, 1, 10), "LAL @ BOS", 202, 103, "Los Angeles Lakers", "Charlie"),
        (503, date(2025, 1, 10), "MIL @ GSW", 203, 104, "Golden State Warriors", "Delta"),
        (504, date(2025, 1, 10), "MIL @ GSW", 204, 105, "Milwaukee Bucks", "Echo"),
        (505, date(2025, 1, 10), "LAL @ BOS", 201, 101, "Boston Celtics", "Alpha"),
        (506, date(2025, 1, 15), "LAL @ BOS", 201, 106, "Boston Celtics", "Foxtrot"),
        (507, date(2025, 1, 20), "LAL @ BOS", 201, 107, "Boston Celtics", "Golf"),
    ]
    for eid, gd, matchup, tid, pid, tname, pname in specs:
        entry = PublicInjuryEntry(
            id=eid,
            source_url="https://example.com/ordering.pdf",
            source_report_date=gd,
            source_report_time=time(17, 0),
            row_number=eid - 500,
            game_date=gd,
            game_time=time(19, 30),
            matchup=matchup,
            player_id=pid,
            player_name=pname,
            team_id=tid,
            team_name=tname,
            status="Out",
            reason_category="Injury",
            raw_reason="general soreness",
            body_part="Knee",
            injury_type="Soreness",
            season="2024-25",
            season_type="regular",
        )
        session.add(entry)
    session.commit()


def _ordering_ids(client, url="/injuries") -> list[int]:
    return [r["id"] for r in client.get(url).json()]


def test_ordering_different_game_dates(client, db):
    _seed_ordering(db)
    dates = [r["game_date"] for r in client.get("/injuries").json()]
    assert dates == sorted(dates, reverse=True), "rows must be ordered by game_date descending"


def test_ordering_same_date_multiple_matchups(client, db):
    _seed_ordering(db)
    data = client.get("/injuries").json()
    jan10 = [r for r in data if r["game_date"] == "2025-01-10"]
    matchups = [r["matchup"] for r in jan10]
    assert matchups == sorted(matchups), "rows on the same date must be ordered by matchup"


def test_ordering_both_teams_in_one_matchup(client, db):
    _seed_ordering(db)
    data = client.get("/injuries").json()
    jan10_bos = [
        r for r in data
        if r["game_date"] == "2025-01-10" and r["matchup"] == "LAL @ BOS"
    ]
    team_names = [r["team_name"] for r in jan10_bos]
    assert team_names == sorted(team_names), "rows in the same matchup must be ordered by team name"


def test_ordering_multiple_players_on_one_team(client, db):
    _seed_ordering(db)
    data = client.get("/injuries").json()
    bos_jan10 = [
        r for r in data
        if r["game_date"] == "2025-01-10"
        and r["matchup"] == "LAL @ BOS"
        and r["team_name"] == "Boston Celtics"
    ]
    player_names = [r["player_name"] for r in bos_jan10]
    assert player_names == ["Alpha", "Bravo"], (
        "players on the same team must be ordered by canonical name"
    )


def test_ordering_full_sequence(client, db):
    _seed_ordering(db)
    ids = _ordering_ids(client)
    assert ids == [507, 506, 505, 501, 502, 503, 504]


def test_ordering_persists_across_pages(client, db):
    _seed_ordering(db)
    page1 = _ordering_ids(client, "/injuries?page=1&page_size=3")
    page2 = _ordering_ids(client, "/injuries?page=2&page_size=3")
    page3 = _ordering_ids(client, "/injuries?page=3&page_size=3")
    full = page1 + page2 + page3
    assert full == [507, 506, 505, 501, 502, 503, 504]


def test_csv_ordering_matches_json(client, db):
    _seed_ordering(db)
    j = _ordering_ids(client, "/injuries")
    c = _csv_ids(client, "/injuries.csv")
    assert j == c, "CSV and JSON must return rows in the same order"


def test_csv_ordering_full_sequence(client, db):
    _seed_ordering(db)
    c = _csv_ids(client, "/injuries.csv")
    assert c == [507, 506, 505, 501, 502, 503, 504]


# ── source_url ────────────────────────────────────────────────────────────────

def test_json_returns_source_url(client, db):
    _seed(db)
    resp = client.get("/injuries")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["source_url"] == "https://example.com/report.pdf"


def test_csv_returns_source_url(client, db):
    _seed(db)
    resp = client.get("/injuries.csv")
    reader = csv.reader(io.StringIO(resp.text))
    header = next(reader)
    src_idx = header.index("source_url")
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0][src_idx] == "https://example.com/report.pdf"


# ── season filter ─────────────────────────────────────────────────────────────

def _seed_multi_season(session: Session):
    """Seed entries across multiple seasons."""
    player = NBAPlayer(id=100, canonical_name="Test Player", name_key="test player")
    team = NBATeam(id=200, canonical_name="Test Team", abbreviation="TST")
    session.add_all([player, team])
    session.flush()

    specs = [
        (501, date(2019, 10, 25), "2019-20", "regular", "2019-20 regular"),
        (502, date(2020, 3, 10), "2019-20", "regular", "2019-20 pre-bubble"),
        (503, date(2020, 7, 30), "2019-20", "playoffs", "2019-20 bubble"),
        (504, date(2020, 9, 30), "2019-20", "playoffs", "2019-20 late bubble"),
        (505, date(2020, 10, 11), "2019-20", "playoffs", "2019-20 last day"),
        (506, date(2020, 12, 25), "2020-21", "regular", "2020-21 early"),
        (507, date(2021, 5, 16), "2020-21", "regular", "2020-21 late regular"),
        (508, date(2021, 7, 10), "2020-21", "playoffs", "2020-21 finals"),
        (509, date(2021, 7, 20), "2020-21", "playoffs", "2020-21 last day"),
        (510, date(2021, 10, 22), "2021-22", "regular", "2021-22 early"),
    ]
    for eid, gd, season, stype, reason in specs:
        entry = PublicInjuryEntry(
            id=eid,
            source_url="https://example.com/report.pdf",
            source_report_date=gd,
            source_report_time=time(17, 0),
            row_number=eid - 500,
            game_date=gd,
            game_time=time(19, 30),
            matchup="TST @ OPP",
            player_id=100,
            player_name="Test Player",
            team_id=200,
            team_name="Test Team",
            status="Out",
            reason_category="Injury",
            raw_reason=reason,
            body_part="Knee",
            injury_type="Soreness",
            season=season,
            season_type=stype,
        )
        session.add(entry)
    session.commit()
    return {s[1]: s[0] for s in specs}


def test_season_filter_normal(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2021-22")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["game_date"] == "2021-10-22"
    assert data[0]["raw_reason"] == "2021-22 early"


def test_season_filter_2019_20(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2019-20")
    data = resp.json()
    assert len(data) == 5


def test_season_filter_2020_21(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2020-21")
    data = resp.json()
    assert len(data) == 4


def test_season_filter_no_match(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2018-19")
    assert resp.json() == []


def test_season_filter_malformed_value(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2024")
    assert resp.status_code == 422
    assert "Unsupported season" in resp.json()["detail"]


def test_season_filter_unsupported_season(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2026-27")
    assert resp.status_code == 422
    assert "2026-27" in resp.json()["detail"]


def test_season_filter_whitespace_stripped(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season= 2021-22 ")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_season_combined_with_start_date(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2019-20&start_date=2020-07-01")
    data = resp.json()
    assert len(data) == 3


def test_season_combined_with_end_date(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2019-20&end_date=2020-03-31")
    data = resp.json()
    assert len(data) == 2


def test_season_combined_with_date_range(client, db):
    _seed_multi_season(db)
    resp = client.get(
        "/injuries?season=2020-21&start_date=2021-05-01&end_date=2021-07-15"
    )
    data = resp.json()
    assert len(data) == 2


def test_season_combined_with_player_id(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2020-21&player_id=100")
    data = resp.json()
    assert len(data) == 4


def test_season_combined_with_status(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2019-20&status=Out")
    data = resp.json()
    assert len(data) == 5
    assert all(r["status"] == "Out" for r in data)


def test_csv_season_parity(client, db):
    _seed_multi_season(db)
    j = client.get("/injuries?season=2019-20").json()
    c = _csv_ids(client, "/injuries.csv?season=2019-20")
    assert [r["id"] for r in j] == c


def test_csv_season_2020_21_parity(client, db):
    _seed_multi_season(db)
    j = client.get("/injuries?season=2020-21").json()
    c = _csv_ids(client, "/injuries.csv?season=2020-21")
    assert [r["id"] for r in j] == c


def test_csv_season_combined_parity(client, db):
    _seed_multi_season(db)
    j = client.get(
        "/injuries?season=2020-21&start_date=2021-05-01"
    ).json()
    c = _csv_ids(
        client,
        "/injuries.csv?season=2020-21&start_date=2021-05-01",
    )
    assert [r["id"] for r in j] == c


def test_csv_season_unsupported_returns_422(client, db):
    resp = client.get("/injuries.csv?season=bad")
    assert resp.status_code == 422


# ── Multi-season filter (list[str]) ──────────────────────────────────────────

def test_multi_season_filter(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2019-20&season=2021-22")
    data = resp.json()
    assert len(data) == 6


def test_single_season_still_works(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2020-21")
    data = resp.json()
    assert len(data) == 4


def test_all_seasons_selected(client, db):
    _seed_multi_season(db)
    resp = client.get(
        "/injuries?season=2018-19&season=2019-20&season=2020-21&season=2021-22"
    )
    data = resp.json()
    assert len(data) == 10


def test_multi_season_or_semantics(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2019-20&season=2020-21")
    data = resp.json()
    assert len(data) == 9


def test_multi_season_invalid_value(client, db):
    resp = client.get("/injuries?season=2019-20&season=bad")
    assert resp.status_code == 422


def test_csv_multi_season_parity(client, db):
    _seed_multi_season(db)
    j = client.get("/injuries?season=2019-20&season=2021-22").json()
    c = _csv_ids(client, "/injuries.csv?season=2019-20&season=2021-22")
    assert [r["id"] for r in j] == c


# ── Season type filter ───────────────────────────────────────────────────────

def _seed_with_season_types(session: Session):
    """Seed entries with season_type values directly on PublicInjuryEntry."""
    player = NBAPlayer(id=100, canonical_name="Test Player", name_key="test player")
    team = NBATeam(id=200, canonical_name="Test Team", abbreviation="TST")
    session.add_all([player, team])
    session.flush()

    entry1 = PublicInjuryEntry(
        id=501,
        source_url="https://example.com/report.pdf",
        source_report_date=date(2024, 10, 25),
        source_report_time=time(17, 0),
        row_number=1,
        game_date=date(2024, 10, 25),
        game_time=time(19, 30),
        matchup="PHX@LAL",
        player_id=100,
        player_name="Test Player",
        team_id=200,
        team_name="Test Team",
        status="Out",
        reason_category="Injury",
        raw_reason="sore knee",
        body_part="Knee",
        injury_type="Soreness",
        season="2024-25",
        season_type="regular",
    )
    entry2 = PublicInjuryEntry(
        id=502,
        source_url="https://example.com/report.pdf",
        source_report_date=date(2024, 10, 4),
        source_report_time=time(17, 0),
        row_number=2,
        game_date=date(2024, 10, 4),
        game_time=time(19, 30),
        matchup="MIN@LAL",
        player_id=100,
        player_name="Test Player",
        team_id=200,
        team_name="Test Team",
        status="Questionable",
        reason_category="Injury",
        raw_reason="ankle pain",
        body_part="Ankle",
        injury_type="Pain",
        season="2024-25",
        season_type="preseason",
    )
    entry3 = PublicInjuryEntry(
        id=503,
        source_url="https://example.com/report.pdf",
        source_report_date=date(2024, 9, 20),
        source_report_time=time(17, 0),
        row_number=3,
        game_date=date(2024, 9, 20),
        game_time=time(19, 30),
        matchup="LAL@GSW",
        player_id=100,
        player_name="Test Player",
        team_id=200,
        team_name="Test Team",
        status="Out",
        reason_category="Injury",
        raw_reason="back tightness",
        body_part="Back",
        injury_type="Tightness",
        season="2024-25",
        season_type=None,
    )
    session.add_all([entry1, entry2, entry3])
    session.commit()


def test_season_type_filter_regular(client, db):
    _seed_with_season_types(db)
    resp = client.get("/injuries?season_type=Regular Season")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["matchup"] == "PHX@LAL"


def test_season_type_filter_preseason(client, db):
    _seed_with_season_types(db)
    resp = client.get("/injuries?season_type=Preseason")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["matchup"] == "MIN@LAL"


def test_season_type_filter_multiple(client, db):
    _seed_with_season_types(db)
    resp = client.get("/injuries?season_type=Regular Season&season_type=Preseason")
    data = resp.json()
    assert len(data) == 2


def test_season_type_excludes_unmatched(client, db):
    _seed_with_season_types(db)
    resp = client.get("/injuries?season_type=Regular Season")
    data = resp.json()
    matchup = [r["matchup"] for r in data]
    assert "LAL@GSW" not in matchup


def test_season_type_invalid_value(client, db):
    resp = client.get("/injuries?season_type=Badminton")
    assert resp.status_code == 422


def test_csv_season_type_parity(client, db):
    _seed_with_season_types(db)
    j = client.get("/injuries?season_type=Regular Season").json()
    c = _csv_ids(client, "/injuries.csv?season_type=Regular Season")
    assert [r["id"] for r in j] == c


def test_csv_season_type_multiple_parity(client, db):
    _seed_with_season_types(db)
    j = client.get(
        "/injuries?season_type=Regular Season&season_type=Preseason"
    ).json()
    c = _csv_ids(
        client,
        "/injuries.csv?season_type=Regular Season&season_type=Preseason",
    )
    assert [r["id"] for r in j] == c


# ── Combined season + season_type ────────────────────────────────────────────

def test_season_and_season_type_combined(client, db):
    _seed_with_season_types(db)
    resp = client.get(
        "/injuries?season=2024-25&season_type=Regular Season"
    )
    data = resp.json()
    assert len(data) == 1
    assert data[0]["matchup"] == "PHX@LAL"


def test_season_and_season_type_no_match(client, db):
    _seed_with_season_types(db)
    resp = client.get(
        "/injuries?season=2023-24&season_type=Regular Season"
    )
    assert resp.json() == []


def test_csv_season_and_season_type_parity(client, db):
    _seed_with_season_types(db)
    j = client.get(
        "/injuries?season=2024-25&season_type=Regular Season"
    ).json()
    c = _csv_ids(
        client,
        "/injuries.csv?season=2024-25&season_type=Regular Season",
    )
    assert [r["id"] for r in j] == c


# ── No duplicates with new filters ──────────────────────────────────────────

def test_no_duplicates_with_multi_season(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2019-20&season=2020-21")
    data = resp.json()
    ids = [r["id"] for r in data]
    assert len(ids) == len(set(ids))


def test_no_duplicates_with_season_type(client, db):
    _seed_with_season_types(db)
    resp = client.get("/injuries?season_type=Regular Season&season_type=Preseason")
    data = resp.json()
    ids = [r["id"] for r in data]
    assert len(ids) == len(set(ids))


def test_no_duplicates_with_combined(client, db):
    _seed_with_season_types(db)
    resp = client.get(
        "/injuries?season=2024-25&season_type=Regular Season&season_type=Preseason"
    )
    data = resp.json()
    ids = [r["id"] for r in data]
    assert len(ids) == len(set(ids))


# ── Backward compatibility: single season query param ───────────────────────

def test_single_season_query_param_comma(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2021-22")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


# ── CSV export: season / season_type values ─────────────────────────────

def _csv_header_and_rows(client, url="/injuries.csv"):
    resp = client.get(url)
    reader = csv.reader(io.StringIO(resp.text))
    header = next(reader)
    rows = list(reader)
    return header, rows


def test_csv_no_previous_status(client, db):
    _seed(db)
    header, _ = _csv_header_and_rows(client)
    assert "previous_status" not in header


def test_csv_no_previous_reason(client, db):
    _seed(db)
    header, _ = _csv_header_and_rows(client)
    assert "previous_reason" not in header


def test_csv_has_season_and_season_type(client, db):
    _seed(db)
    header, _ = _csv_header_and_rows(client)
    assert "season" in header
    assert "season_type" in header


def test_csv_season_correct_for_regular_season_entry(client, db):
    _seed(db)
    header, rows = _csv_header_and_rows(client)
    season_idx = header.index("season")
    assert rows[0][season_idx] == "2024-25"


def test_csv_season_across_seasons(client, db):
    """Entries spanning 2019-20 through 2021-22 get correct season labels from PublicInjuryEntry."""
    _seed_multi_season(db)
    header, rows = _csv_header_and_rows(client)
    season_idx = header.index("season")
    date_idx = header.index("game_date")
    season_dates = {}
    for row in rows:
        season_dates[row[date_idx]] = row[season_idx]
    assert season_dates["2019-10-25"] == "2019-20"
    assert season_dates["2020-07-30"] == "2019-20"
    assert season_dates["2020-12-25"] == "2020-21"
    assert season_dates["2021-10-22"] == "2021-22"


def test_csv_season_type_values_direct_from_entry(client, db):
    """CSV season_type comes directly from PublicInjuryEntry, not schedule lookup."""
    _seed_with_season_types(db)
    header, rows = _csv_header_and_rows(client)
    st_idx = header.index("season_type")
    matchup_idx = header.index("matchup")
    results = {row[matchup_idx]: row[st_idx] for row in rows}
    assert results["PHX@LAL"] == "Regular Season"
    assert results["MIN@LAL"] == "Preseason"


def test_csv_filtered_by_season_reflects_filter(client, db):
    _seed_multi_season(db)
    header, rows = _csv_header_and_rows(client, "/injuries.csv?season=2020-21")
    season_idx = header.index("season")
    assert len(rows) == 4
    assert all(r[season_idx] == "2020-21" for r in rows)


def test_csv_filtered_by_season_type_reflects_filter(client, db):
    _seed_with_season_types(db)
    header, rows = _csv_header_and_rows(
        client, "/injuries.csv?season_type=Regular Season"
    )
    st_idx = header.index("season_type")
    assert len(rows) == 1
    assert rows[0][st_idx] == "Regular Season"


def test_csv_combined_season_and_season_type_filter(client, db):
    _seed_with_season_types(db)
    header, rows = _csv_header_and_rows(
        client, "/injuries.csv?season=2024-25&season_type=Regular Season"
    )
    season_idx = header.index("season")
    st_idx = header.index("season_type")
    assert len(rows) == 1
    assert rows[0][season_idx] == "2024-25"
    assert rows[0][st_idx] == "Regular Season"


# ── Focused filtering tests ─────────────────────────────────────────────────


def test_focused_single_season(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2021-22")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["game_date"] == "2021-10-22"


def test_focused_multiple_seasons(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2019-20&season=2021-22")
    data = resp.json()
    assert len(data) == 6
    dates = {r["game_date"] for r in data}
    assert dates == {
        "2019-10-25", "2020-03-10", "2020-07-30",
        "2020-09-30", "2020-10-11", "2021-10-22",
    }


def test_focused_single_season_type(client, db):
    _seed_with_season_types(db)
    resp = client.get("/injuries?season_type=Regular Season")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["matchup"] == "PHX@LAL"


def test_focused_multiple_season_types(client, db):
    _seed_with_season_types(db)
    resp = client.get(
        "/injuries?season_type=Regular Season&season_type=Preseason"
    )
    data = resp.json()
    assert len(data) == 2
    matchups = {r["matchup"] for r in data}
    assert matchups == {"PHX@LAL", "MIN@LAL"}


def test_focused_season_and_season_type_combined(client, db):
    _seed_with_season_types(db)
    resp = client.get(
        "/injuries?season=2024-25&season_type=Regular Season"
    )
    data = resp.json()
    assert len(data) == 1
    assert data[0]["matchup"] == "PHX@LAL"
    assert data[0]["game_date"] == "2024-10-25"


def test_focused_no_duplicates_with_season(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2019-20&season=2020-21")
    ids = [r["id"] for r in resp.json()]
    assert len(ids) == len(set(ids))


def test_focused_no_duplicates_with_season_type(client, db):
    _seed_with_season_types(db)
    resp = client.get(
        "/injuries?season_type=Regular Season&season_type=Preseason"
    )
    ids = [r["id"] for r in resp.json()]
    assert len(ids) == len(set(ids))


def test_focused_no_duplicates_with_combined(client, db):
    _seed_with_season_types(db)
    resp = client.get(
        "/injuries?season=2024-25&season_type=Regular Season&season_type=Preseason"
    )
    ids = [r["id"] for r in resp.json()]
    assert len(ids) == len(set(ids))


def test_focused_json_csv_parity_season(client, db):
    _seed_multi_season(db)
    j = client.get("/injuries?season=2019-20").json()
    c = _csv_ids(client, "/injuries.csv?season=2019-20")
    assert [r["id"] for r in j] == c


def test_focused_json_csv_parity_season_type(client, db):
    _seed_with_season_types(db)
    j = client.get("/injuries?season_type=Regular Season").json()
    c = _csv_ids(client, "/injuries.csv?season_type=Regular Season")
    assert [r["id"] for r in j] == c


def test_focused_json_csv_parity_combined(client, db):
    _seed_with_season_types(db)
    j = client.get(
        "/injuries?season=2024-25&season_type=Regular Season"
    ).json()
    c = _csv_ids(
        client,
        "/injuries.csv?season=2024-25&season_type=Regular Season",
    )
    assert [r["id"] for r in j] == c


def test_focused_csv_season_values(client, db):
    """CSV season column comes from PublicInjuryEntry.season."""
    _seed_multi_season(db)
    header, rows = _csv_header_and_rows(client)
    season_idx = header.index("season")
    date_idx = header.index("game_date")
    season_by_date = {row[date_idx]: row[season_idx] for row in rows}
    assert season_by_date["2019-10-25"] == "2019-20"
    assert season_by_date["2020-07-30"] == "2019-20"
    assert season_by_date["2020-12-25"] == "2020-21"
    assert season_by_date["2021-10-22"] == "2021-22"


def test_focused_csv_season_type_values(client, db):
    """CSV season_type column comes from PublicInjuryEntry.season_type."""
    _seed_with_season_types(db)
    header, rows = _csv_header_and_rows(client)
    st_idx = header.index("season_type")
    matchup_idx = header.index("matchup")
    results = {row[matchup_idx]: row[st_idx] for row in rows}
    assert results["PHX@LAL"] == "Regular Season"
    assert results["MIN@LAL"] == "Preseason"
