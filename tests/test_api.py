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
    NBAInjuryCondition,
    NBAPlayer,
    NBAReport,
    NBAReportCandidate,
    NBAReportEntry,
    NBATeam,
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
    """Insert minimal NBAReportEntry rows with supporting FK rows.

    Returns list of (entry_id, condition_indices) pairs.
    """
    if condition_counts is None:
        condition_counts = {1: 1}

    player = NBAPlayer(id=100, canonical_name="Test Player", name_key="test player")
    team = NBATeam(id=200, canonical_name="Test Team", abbreviation="TST")
    candidate = NBAReportCandidate(
        id=300,
        source_url="https://example.com/report.pdf",
        report_date=date(2025, 1, 15),
        status="parsed",
    )
    report = NBAReport(
        id=400,
        candidate_id=300,
        report_date=date(2025, 1, 15),
        report_time=time(17, 0),
        source_url="https://example.com/report.pdf",
        content_hash="abc123",
        content=b"dummy",
        content_type="application/pdf",
        byte_length=5,
        parse_status="parsed",
    )
    session.add_all([player, team, candidate, report])
    session.flush()

    entries = []
    for i in range(1, entry_count + 1):
        entry = NBAReportEntry(
            id=500 + i,
            report_id=400,
            page_number=1,
            row_number=i,
            team_id=200,
            player_id=100,
            entry_type="player",
            game_date=date(2025, 1, 15),
            game_time=time(19, 30),
            matchup="TST @ OPP",
            team_name_raw="TST",
            player_name_raw="Test Player",
            status="Out",
            reason_category="Injury",
            raw_reason="sore left knee",
            previous_status="Questionable",
            previous_reason="knee soreness",
            raw_row_text="raw row",
        )
        session.add(entry)
        session.flush()
        indices = condition_counts.get(i, [1])
        if isinstance(indices, int):
            indices = [indices]
        for idx, bp in zip(indices, _body_parts_for(indices), strict=True):
            cond = NBAInjuryCondition(
                report_entry_id=entry.id,
                condition_index=idx,
                body_part=bp,
                injury_type="Soreness",
                normalized_reason=f"sore {bp}",
                classification_version="v1",
                is_injury=True,
            )
            session.add(cond)
        entries.append((entry.id, indices))
    session.commit()
    return entries


def _body_parts_for(indices):
    parts = ["Knee", "Ankle", "Back"]
    return [parts[(i - 1) % len(parts)] for i in indices]


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
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0][6] == "Test Player"  # player_name
    assert rows[0][12] == "Knee"  # body_part


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
    assert data[0]["id"] == 503
    assert data[1]["id"] == 504


def test_pagination_beyond_last_page(client, db):
    _seed(db, entry_count=3)
    resp = client.get("/injuries?page=10&page_size=10")
    assert resp.json() == []


# ── Filters ──────────────────────────────────────────────────────────────────

def test_filter_by_player_id(client, db):
    player2 = NBAPlayer(id=101, canonical_name="Other Player", name_key="other player")
    db.add(player2)
    db.flush()
    entry2 = NBAReportEntry(
        id=510,
        report_id=400,
        page_number=1,
        row_number=10,
        team_id=200,
        player_id=101,
        entry_type="player",
        game_date=date(2025, 1, 16),
        matchup="TST @ OPP",
        team_name_raw="TST",
        player_name_raw="Other Player",
        status="Probable",
        raw_reason="back tightness",
        raw_row_text="raw",
    )
    db.add(entry2)
    db.commit()

    resp = client.get("/injuries?player_id=101")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["player_id"] == 101


def test_filter_by_team_id(client, db):
    _seed(db)
    team2 = NBATeam(id=201, canonical_name="Other Team", abbreviation="OTH")
    db.add(team2)
    db.flush()
    entry2 = NBAReportEntry(
        id=511,
        report_id=400,
        page_number=1,
        row_number=11,
        team_id=201,
        player_id=100,
        entry_type="player",
        game_date=date(2025, 1, 16),
        matchup="OTH @ OPP",
        team_name_raw="OTH",
        player_name_raw="Test Player",
        status="Doubtful",
        raw_reason="ankle pain",
        raw_row_text="raw",
    )
    db.add(entry2)
    db.commit()

    resp = client.get("/injuries?team_id=201")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["team_id"] == 201


def test_filter_by_body_part(client, db):
    _seed(db, condition_counts={1: [1, 2]})
    resp_knee = client.get("/injuries?body_part=Knee")
    assert len(resp_knee.json()) == 1

    resp_ankle = client.get("/injuries?body_part=Ankle")
    assert len(resp_ankle.json()) == 1

    resp_back = client.get("/injuries?body_part=Back")
    assert len(resp_back.json()) == 0


def test_body_part_filter_matches_any_condition(client, db):
    _seed(db, condition_counts={1: [1, 2]})
    resp = client.get("/injuries?body_part=Ankle")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["body_part"] == "Knee"


def test_filter_combined(client, db):
    _seed(db)
    player2 = NBAPlayer(id=101, canonical_name="Other Player", name_key="other player")
    db.add(player2)
    db.flush()
    entry2 = NBAReportEntry(
        id=512,
        report_id=400,
        page_number=1,
        row_number=12,
        team_id=200,
        player_id=101,
        entry_type="player",
        game_date=date(2025, 1, 16),
        matchup="TST @ OPP",
        team_name_raw="TST",
        player_name_raw="Other Player",
        status="Out",
        raw_reason="ankle sprain",
        raw_row_text="raw",
    )
    db.add(entry2)
    cond = NBAInjuryCondition(
        report_entry_id=512,
        condition_index=1,
        body_part="Ankle",
        injury_type="Sprain",
        normalized_reason="ankle sprain",
        classification_version="v1",
        is_injury=True,
    )
    db.add(cond)
    db.commit()

    resp = client.get("/injuries?player_id=100&body_part=Knee")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["player_id"] == 100


# ── Duplicate prevention ────────────────────────────────────────────────────

def test_no_duplicate_rows_with_multiple_conditions(client, db):
    _seed(db, condition_counts={1: [1, 2, 3]})
    resp = client.get("/injuries")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["body_part"] == "Knee"
    assert data[0]["injury_type"] == "Soreness"


def test_csv_no_duplicate_rows(client, db):
    _seed(db, condition_counts={1: [1, 2]})
    resp = client.get("/injuries.csv")
    reader = csv.reader(io.StringIO(resp.text))
    next(reader)
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0][12] == "Knee"


# ── start_date / end_date / status filters ─────────────────────────────────────

def _seed_multi_date(session: Session):
    """Seed three entries on different dates and statuses."""
    player = NBAPlayer(id=100, canonical_name="Test Player", name_key="test player")
    team = NBATeam(id=200, canonical_name="Test Team", abbreviation="TST")
    candidate = NBAReportCandidate(
        id=300,
        source_url="https://example.com/report.pdf",
        report_date=date(2025, 1, 15),
        status="parsed",
    )
    report = NBAReport(
        id=400,
        candidate_id=300,
        report_date=date(2025, 1, 15),
        report_time=time(17, 0),
        source_url="https://example.com/report.pdf",
        content_hash="abc123",
        content=b"dummy",
        content_type="application/pdf",
        byte_length=5,
        parse_status="parsed",
    )
    session.add_all([player, team, candidate, report])
    session.flush()

    specs = [
        (501, date(2025, 1, 10), "Out"),
        (502, date(2025, 1, 15), "Questionable"),
        (503, date(2025, 1, 20), "Out"),
    ]
    for eid, gd, st in specs:
        entry = NBAReportEntry(
            id=eid,
            report_id=400,
            page_number=1,
            row_number=eid - 500,
            team_id=200,
            player_id=100,
            entry_type="player",
            game_date=gd,
            game_time=time(19, 30),
            matchup="TST @ OPP",
            team_name_raw="TST",
            player_name_raw="Test Player",
            status=st,
            reason_category="Injury",
            raw_reason="sore knee",
            raw_row_text="raw",
        )
        session.add(entry)
        session.flush()
        cond = NBAInjuryCondition(
            report_entry_id=eid,
            condition_index=1,
            body_part="Knee",
            injury_type="Soreness",
            normalized_reason="sore knee",
            classification_version="v1",
            is_injury=True,
        )
        session.add(cond)
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


def test_injury_type_filter_matches_any_condition(client, db):
    _seed(db, condition_counts={1: [1, 2]})
    resp_knee = client.get("/injuries?injury_type=Soreness")
    assert len(resp_knee.json()) == 1


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
    _seed(db, condition_counts={1: [1, 2]})
    resp = client.get(
        "/injuries?injury_type=Soreness&reason_search=knee&team_id=200"
    )
    data = resp.json()
    assert len(data) == 1
    assert data[0]["injury_type"] == "Soreness"


# ── duplicate prevention with new filters ─────────────────────────────────────

def test_injury_type_no_duplicates_with_multiple_conditions(client, db):
    _seed(db, condition_counts={1: [1, 2, 3]})
    resp = client.get("/injuries?injury_type=Soreness")
    data = resp.json()
    assert len(data) == 1


def test_reason_search_no_duplicates_with_multiple_conditions(client, db):
    _seed(db, condition_counts={1: [1, 2, 3]})
    resp = client.get("/injuries?reason_search=knee")
    data = resp.json()
    assert len(data) == 1


# ── CSV / JSON parity for new filters ─────────────────────────────────────────

def test_csv_injury_type_parity(client, db):
    _seed(db)
    j = client.get("/injuries?injury_type=Soreness").json()
    c = _csv_ids(client, "/injuries.csv?injury_type=Soreness")
    assert [r["id"] for r in j] == c


def test_csv_reason_search_parity(client, db):
    _seed(db)
    j = client.get("/injuries?reason_search=knee").json()
    c = _csv_ids(client, "/injuries.csv?reason_search=knee")
    assert [r["id"] for r in j] == c


def test_csv_combined_new_filters_parity(client, db):
    _seed(db)
    j = client.get(
        "/injuries?injury_type=Soreness&reason_search=knee"
    ).json()
    c = _csv_ids(
        client,
        "/injuries.csv?injury_type=Soreness&reason_search=knee",
    )
    assert [r["id"] for r in j] == c


# ── season filter ─────────────────────────────────────────────────────────────

def _seed_multi_season(session: Session):
    """Seed entries across normal and COVID-affected seasons.

    Returns dict mapping game_date -> entry_id for easy lookup.
    """
    player = NBAPlayer(id=100, canonical_name="Test Player", name_key="test player")
    team = NBATeam(id=200, canonical_name="Test Team", abbreviation="TST")
    candidate = NBAReportCandidate(
        id=300,
        source_url="https://example.com/report.pdf",
        report_date=date(2020, 1, 1),
        status="parsed",
    )
    report = NBAReport(
        id=400,
        candidate_id=300,
        report_date=date(2020, 1, 1),
        report_time=time(17, 0),
        source_url="https://example.com/report.pdf",
        content_hash="abc123",
        content=b"dummy",
        content_type="application/pdf",
        byte_length=5,
        parse_status="parsed",
    )
    session.add_all([player, team, candidate, report])
    session.flush()

    specs = [
        (501, date(2019, 10, 25), "2019-20 regular"),
        (502, date(2020, 3, 10), "2019-20 pre-bubble"),
        (503, date(2020, 7, 30), "2019-20 bubble"),       # summer 2020 bubble games
        (504, date(2020, 9, 30), "2019-20 late bubble"),   # fall 2020 bubble/finals
        (505, date(2020, 10, 11), "2019-20 last day"),     # last day of 2019-20
        (506, date(2020, 12, 25), "2020-21 early"),        # Christmas 2020
        (507, date(2021, 5, 16), "2020-21 late regular"),
        (508, date(2021, 7, 10), "2020-21 finals"),        # July 2021 finals
        (509, date(2021, 7, 20), "2020-21 last day"),      # last day of 2020-21
        (510, date(2021, 10, 22), "2021-22 early"),
    ]
    for eid, gd, reason in specs:
        entry = NBAReportEntry(
            id=eid,
            report_id=400,
            page_number=1,
            row_number=eid - 500,
            team_id=200,
            player_id=100,
            entry_type="player",
            game_date=gd,
            game_time=time(19, 30),
            matchup="TST @ OPP",
            team_name_raw="TST",
            player_name_raw="Test Player",
            status="Out",
            reason_category="Injury",
            raw_reason=reason,
            raw_row_text="raw",
        )
        session.add(entry)
        session.flush()
        cond = NBAInjuryCondition(
            report_entry_id=eid,
            condition_index=1,
            body_part="Knee",
            injury_type="Soreness",
            normalized_reason="sore knee",
            classification_version="v1",
            is_injury=True,
        )
        session.add(cond)
    session.commit()
    return {gd: eid for _, gd, _ in [(None, s[1], None) for s in specs]}


def test_season_filter_normal(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2021-22")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["game_date"] == "2021-10-22"
    assert data[0]["raw_reason"] == "2021-22 early"


def test_season_filter_2019_20_includes_bubble(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2019-20")
    data = resp.json()
    dates = [r["game_date"] for r in data]
    # Should include regular, bubble (July), late bubble (Sept), and last day (Oct 11)
    assert len(data) == 5
    assert "2019-10-25" in dates
    assert "2020-03-10" in dates
    assert "2020-07-30" in dates  # summer 2020 bubble
    assert "2020-09-30" in dates  # fall 2020
    assert "2020-10-11" in dates  # last day of 2019-20


def test_season_filter_2020_21_includes_july_games(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2020-21")
    data = resp.json()
    dates = [r["game_date"] for r in data]
    assert len(data) == 4
    assert "2020-12-25" in dates
    assert "2021-05-16" in dates
    assert "2021-07-10" in dates  # July 2021 finals
    assert "2021-07-20" in dates  # last day


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
    dates = [r["game_date"] for r in data]
    assert len(data) == 3
    assert all(d >= "2020-07-01" for d in dates)


def test_season_combined_with_end_date(client, db):
    _seed_multi_season(db)
    resp = client.get("/injuries?season=2019-20&end_date=2020-03-31")
    data = resp.json()
    dates = [r["game_date"] for r in data]
    assert len(data) == 2
    assert all(d <= "2020-03-31" for d in dates)


def test_season_combined_with_date_range(client, db):
    _seed_multi_season(db)
    resp = client.get(
        "/injuries?season=2020-21&start_date=2021-05-01&end_date=2021-07-15"
    )
    data = resp.json()
    dates = [r["game_date"] for r in data]
    assert len(data) == 2
    assert all("2021-05-01" <= d <= "2021-07-15" for d in dates)


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
    candidate = NBAReportCandidate(
        id=300,
        source_url="https://example.com/ordering.pdf",
        report_date=date(2025, 1, 20),
        status="parsed",
    )
    report = NBAReport(
        id=400,
        candidate_id=300,
        report_date=date(2025, 1, 20),
        report_time=time(17, 0),
        source_url="https://example.com/ordering.pdf",
        content_hash="order123",
        content=b"dummy",
        content_type="application/pdf",
        byte_length=5,
        parse_status="parsed",
    )
    session.add_all(players + teams + [candidate, report])
    session.flush()

    # (id, game_date, matchup, team_id, player_id)
    specs = [
        (501, date(2025, 1, 10), "LAL @ BOS", 201, 102),  # BOS, Bravo
        (502, date(2025, 1, 10), "LAL @ BOS", 202, 103),  # LAL, Charlie
        (503, date(2025, 1, 10), "MIL @ GSW", 203, 104),  # GSW, Delta
        (504, date(2025, 1, 10), "MIL @ GSW", 204, 105),  # MIL, Echo
        (505, date(2025, 1, 10), "LAL @ BOS", 201, 101),  # BOS, Alpha (second player)
        (506, date(2025, 1, 15), "LAL @ BOS", 201, 106),  # BOS, Foxtrot
        (507, date(2025, 1, 20), "LAL @ BOS", 201, 107),  # BOS, Golf
    ]
    for eid, gd, matchup, tid, pid in specs:
        entry = NBAReportEntry(
            id=eid,
            report_id=400,
            page_number=1,
            row_number=eid - 500,
            team_id=tid,
            player_id=pid,
            entry_type="player",
            game_date=gd,
            game_time=time(19, 30),
            matchup=matchup,
            team_name_raw=teams[tid - 201].abbreviation,
            player_name_raw=players[pid - 101].canonical_name,
            status="Out",
            reason_category="Injury",
            raw_reason="general soreness",
            raw_row_text="raw",
        )
        session.add(entry)
        session.flush()
        cond = NBAInjuryCondition(
            report_entry_id=eid,
            condition_index=1,
            body_part="Knee",
            injury_type="Soreness",
            normalized_reason="sore knee",
            classification_version="v1",
            is_injury=True,
        )
        session.add(cond)
    session.commit()


def _ordering_ids(client, url="/injuries") -> list[int]:
    return [r["id"] for r in client.get(url).json()]


def test_ordering_different_game_dates(client, db):
    _seed_ordering(db)
    ids = _ordering_ids(client)
    dates = [r["game_date"] for r in client.get("/injuries").json()]
    assert dates == sorted(dates), "rows must be ordered by game_date ascending"


def test_ordering_same_date_multiple_matchups(client, db):
    _seed_ordering(db)
    data = client.get("/injuries").json()
    # All 2025-01-10 rows: LAL @ BOS comes before MIL @ GSW lexicographically
    jan10 = [r for r in data if r["game_date"] == "2025-01-10"]
    matchups = [r["matchup"] for r in jan10]
    assert matchups == sorted(matchups), "rows on the same date must be ordered by matchup"


def test_ordering_both_teams_in_one_matchup(client, db):
    _seed_ordering(db)
    data = client.get("/injuries").json()
    # Within LAL @ BOS on 2025-01-10: Boston Celtics (B) before Los Angeles Lakers (L)
    jan10_bos = [
        r for r in data
        if r["game_date"] == "2025-01-10" and r["matchup"] == "LAL @ BOS"
    ]
    team_names = [r["team_name"] for r in jan10_bos]
    assert team_names == sorted(team_names), "rows in the same matchup must be ordered by team name"


def test_ordering_multiple_players_on_one_team(client, db):
    _seed_ordering(db)
    data = client.get("/injuries").json()
    # Boston Celtics on 2025-01-10 should have Alpha before Bravo
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
    # Expected order by (game_date, matchup, team, player):
    # 2025-01-10 LAL@BOS BOS Alpha=101, BOS Bravo=102, LAL Charlie=103
    # 2025-01-10 MIL@GSW GSW Delta=104, MIL Echo=105
    # 2025-01-15 LAL@BOS BOS Foxtrot=106
    # 2025-01-20 LAL@BOS BOS Golf=107
    assert ids == [505, 501, 502, 503, 504, 506, 507]


def test_ordering_persists_across_pages(client, db):
    _seed_ordering(db)
    page1 = _ordering_ids(client, "/injuries?page=1&page_size=3")
    page2 = _ordering_ids(client, "/injuries?page=2&page_size=3")
    page3 = _ordering_ids(client, "/injuries?page=3&page_size=3")
    full = page1 + page2 + page3
    assert full == [505, 501, 502, 503, 504, 506, 507]


def test_csv_ordering_matches_json(client, db):
    _seed_ordering(db)
    j = _ordering_ids(client, "/injuries")
    c = _csv_ids(client, "/injuries.csv")
    assert j == c, "CSV and JSON must return rows in the same order"


def test_csv_ordering_full_sequence(client, db):
    _seed_ordering(db)
    c = _csv_ids(client, "/injuries.csv")
    assert c == [505, 501, 502, 503, 504, 506, 507]


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


def test_source_url_none_when_no_report(client, db):
    """source_url should be None if the linked report is missing."""
    player = NBAPlayer(id=100, canonical_name="Test Player", name_key="test player")
    team = NBATeam(id=200, canonical_name="Test Team", abbreviation="TST")
    candidate = NBAReportCandidate(
        id=300,
        source_url="https://example.com/report.pdf",
        report_date=date(2025, 1, 15),
        status="discovered",
    )
    session = db
    session.add_all([player, team, candidate])
    session.flush()
    entry = NBAReportEntry(
        id=501,
        report_id=0,
        page_number=1,
        row_number=1,
        team_id=200,
        player_id=100,
        entry_type="player",
        game_date=date(2025, 1, 15),
        game_time=time(19, 30),
        matchup="TST @ OPP",
        team_name_raw="TST",
        player_name_raw="Test Player",
        status="Out",
        raw_reason="sore knee",
        raw_row_text="raw",
    )
    session.add(entry)
    session.commit()
    resp = client.get("/injuries")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["source_url"] is None
