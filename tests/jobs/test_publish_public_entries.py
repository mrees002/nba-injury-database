"""Focused tests for the public injury entry publisher."""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session

from app.db.base import Base
from app.jobs.publish_public_entries import publish_public_entries
from app.models.nba import (
    NBAInjuryCondition,
    NBAPlayer,
    NBAReport,
    NBAReportCandidate,
    NBAReportEntry,
    NBAScheduleGame,
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


def _seed_base(session: Session):
    """Insert shared player, team, candidate, report."""
    player = NBAPlayer(id=100, canonical_name="LeBron James", name_key="lebron james")
    team = NBATeam(id=200, canonical_name="Los Angeles Lakers", abbreviation="LAL")
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
        report_time=time(17, 30),
        source_url="https://example.com/report.pdf",
        content_hash="abc123",
        content=b"dummy",
        content_type="application/pdf",
        byte_length=5,
        parse_status="parsed",
    )
    session.add_all([player, team, candidate, report])
    session.flush()


def _seed_entry(session: Session, **overrides):
    """Insert an NBAReportEntry with condition_index=1."""
    defaults = dict(
        id=500,
        report_id=400,
        page_number=1,
        row_number=1,
        team_id=200,
        player_id=100,
        entry_type="player",
        game_date=date(2025, 1, 15),
        game_time=time(19, 30),
        matchup="LAL@BOS",
        team_name_raw="LAL",
        player_name_raw="LeBron James",
        status="Out",
        reason_category="Injury",
        raw_reason="sore left knee",
        raw_row_text="raw row",
    )
    defaults.update(overrides)
    entry = NBAReportEntry(**defaults)
    session.add(entry)
    session.flush()
    cond = NBAInjuryCondition(
        report_entry_id=entry.id,
        condition_index=1,
        body_part="Knee",
        injury_type="Soreness",
        normalized_reason="sore left knee",
        classification_version="v1",
        is_injury=True,
    )
    session.add(cond)
    session.flush()
    return entry


def _seed_schedule(session: Session, game_date: date, matchup: str, season: str, season_type: str):
    sg = NBAScheduleGame(
        season=season,
        game_date=game_date,
        season_type=season_type,
        away_team=matchup.split("@")[0],
        home_team=matchup.split("@")[1],
        matchup=matchup,
    )
    session.add(sg)
    session.flush()


# ── Correct publication fields ───────────────────────────────────────────────


def test_publishes_correct_fields(db):
    _seed_base(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")
    _seed_entry(db)

    result = publish_public_entries(db)
    db.flush()

    assert result.inserted == 1
    rows = db.query(PublicInjuryEntry).all()
    assert len(rows) == 1
    pub = rows[0]
    assert pub.source_url == "https://example.com/report.pdf"
    assert pub.source_report_date == date(2025, 1, 15)
    assert pub.source_report_time == time(17, 30)
    assert pub.row_number == 1
    assert pub.game_date == date(2025, 1, 15)
    assert pub.game_time == time(19, 30)
    assert pub.matchup == "LAL@BOS"
    assert pub.player_id == 100
    assert pub.player_name == "LeBron James"
    assert pub.team_id == 200
    assert pub.team_name == "Los Angeles Lakers"
    assert pub.status == "Out"
    assert pub.raw_reason == "sore left knee"
    assert pub.reason_category == "Injury"
    assert pub.body_part == "Knee"
    assert pub.injury_type == "Soreness"
    assert pub.season == "2024-25"
    assert pub.season_type == "regular"
    assert pub.updated_at is not None


# ── condition_index=1 only ───────────────────────────────────────────────────


def test_only_condition_index_1_is_published(db):
    """Entries with condition_index != 1 are not published."""
    _seed_base(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")
    entry = _seed_entry(db)

    # Add a second condition with index=2 on the same entry
    cond2 = NBAInjuryCondition(
        report_entry_id=entry.id,
        condition_index=2,
        body_part="Ankle",
        injury_type="Sprain",
        normalized_reason="ankle sprain",
        classification_version="v1",
        is_injury=True,
    )
    db.add(cond2)
    db.flush()

    result = publish_public_entries(db)
    db.flush()

    # Only one published row (from condition_index=1)
    assert result.inserted == 1
    pub = db.query(PublicInjuryEntry).one()
    assert pub.body_part == "Knee"
    assert pub.injury_type == "Soreness"


def test_entry_without_condition_index_1_is_excluded(db):
    """An entry with no condition_index=1 is not published."""
    _seed_base(db)
    entry = NBAReportEntry(
        id=501,
        report_id=400,
        page_number=1,
        row_number=2,
        team_id=200,
        player_id=100,
        entry_type="player",
        game_date=date(2025, 1, 15),
        matchup="LAL@BOS",
        team_name_raw="LAL",
        player_name_raw="LeBron James",
        status="Out",
        raw_reason="back tightness",
        raw_row_text="raw",
    )
    db.add(entry)
    db.flush()
    # Only add condition_index=2, no index=1
    cond = NBAInjuryCondition(
        report_entry_id=entry.id,
        condition_index=2,
        body_part="Back",
        injury_type="Tightness",
        normalized_reason="back tightness",
        classification_version="v1",
        is_injury=True,
    )
    db.add(cond)
    db.commit()

    result = publish_public_entries(db)
    db.flush()

    assert result.inserted == 0
    assert db.query(PublicInjuryEntry).count() == 0


# ── 2018-19 excluded ────────────────────────────────────────────────────────


def test_2018_19_entries_are_excluded(db):
    """Entries from the 2018-19 season (before cutoff) are not published."""
    _seed_base(db)
    _seed_entry(db, id=510, row_number=10, game_date=date(2019, 4, 10), matchup="LAL@GSW")
    _seed_entry(db, id=511, row_number=11, game_date=date(2019, 10, 25), matchup="LAL@GSW")

    result = publish_public_entries(db)
    db.flush()

    # Only the 2019-10-25 entry is published (on or after cutoff 2019-10-22)
    assert result.inserted == 1
    pub = db.query(PublicInjuryEntry).one()
    assert pub.game_date == date(2019, 10, 25)


def test_exact_cutoff_date_is_included(db):
    """Entry on 2019-10-22 (first day of 2019-20) is included."""
    _seed_base(db)
    _seed_entry(db, id=512, row_number=12, game_date=date(2019, 10, 22), matchup="LAL@GSW")

    result = publish_public_entries(db)
    db.flush()

    assert result.inserted == 1


# ── Source report timestamp retained ─────────────────────────────────────────


def test_source_report_timestamp_is_retained(db):
    """source_report_date and source_report_time come from NBAReport, not entry."""
    _seed_base(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")
    _seed_entry(db)

    publish_public_entries(db)
    db.flush()

    pub = db.query(PublicInjuryEntry).one()
    # These match the NBAReport values, not the entry's game_date/game_time
    assert pub.source_report_date == date(2025, 1, 15)
    assert pub.source_report_time == time(17, 30)


# ── Season and season_type ───────────────────────────────────────────────────


def test_season_and_season_type_from_schedule(db):
    _seed_base(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")
    _seed_entry(db)

    publish_public_entries(db)
    db.flush()

    pub = db.query(PublicInjuryEntry).one()
    assert pub.season == "2024-25"
    assert pub.season_type == "regular"


def test_season_type_playoffs(db):
    _seed_base(db)
    _seed_schedule(db, date(2025, 5, 20), "LAL@BOS", "2024-25", "playoffs")
    _seed_entry(db, id=520, row_number=20, game_date=date(2025, 5, 20), matchup="LAL@BOS")

    publish_public_entries(db)
    db.flush()

    pub = db.query(PublicInjuryEntry).one()
    assert pub.season == "2024-25"
    assert pub.season_type == "playoffs"


# ── Missing schedule match remains nullable ──────────────────────────────────


def test_missing_schedule_match_season_is_null(db):
    """When no NBAScheduleGame matches, season and season_type are NULL."""
    _seed_base(db)
    # No schedule game seeded
    _seed_entry(db)

    publish_public_entries(db)
    db.flush()

    pub = db.query(PublicInjuryEntry).one()
    assert pub.season is None
    assert pub.season_type is None


# ── Idempotency ─────────────────────────────────────────────────────────────


def test_idempotent_republishing(db):
    """Running publish twice does not duplicate rows."""
    _seed_base(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")
    _seed_entry(db)

    r1 = publish_public_entries(db)
    db.flush()
    assert r1.inserted == 1

    r2 = publish_public_entries(db)
    db.flush()
    assert r2.inserted == 0
    assert r2.updated == 1

    assert db.query(PublicInjuryEntry).count() == 1


def test_idempotent_preserves_source_url_and_row_number(db):
    """Re-publishing updates in place, preserving the UNIQUE key."""
    _seed_base(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")
    _seed_entry(db)

    publish_public_entries(db)
    db.flush()
    pub = db.query(PublicInjuryEntry).one()
    original_id = pub.id

    # Re-publish
    publish_public_entries(db)
    db.flush()
    pub = db.query(PublicInjuryEntry).one()
    assert pub.id == original_id  # same row, not a new one


# ── Dry-run mode ─────────────────────────────────────────────────────────────


def test_dry_run_does_not_modify_data(db):
    _seed_base(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")
    _seed_entry(db)

    result = publish_public_entries(db, dry_run=True)

    assert result.inserted == 1
    assert result.total_canonical == 1
    # Nothing persisted
    assert db.query(PublicInjuryEntry).count() == 0


# ── Multiple entries ────────────────────────────────────────────────────────


def test_multiple_entries_published(db):
    _seed_base(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")
    _seed_entry(db, id=500, row_number=1, game_date=date(2025, 1, 15))
    _seed_entry(db, id=501, row_number=2, game_date=date(2025, 1, 16), matchup="LAL@GSW")

    result = publish_public_entries(db)
    db.flush()

    assert result.inserted == 2
    assert db.query(PublicInjuryEntry).count() == 2


# ── 2018-19 audit query logic ──────────────────────────────────────────────


def test_audit_query_includes_2018_19_partial_window(db):
    """The audit query should include entries from 2018-12-20 through 2019-10-21."""
    from scripts.audit_2018_19_restoration import _build_2018_19_query

    _seed_base(db)
    _seed_entry(db, id=600, row_number=100, game_date=date(2019, 1, 15), matchup="LAL@GSW")

    rows = _build_2018_19_query(db).all()
    assert len(rows) == 1
    entry = rows[0][0]
    assert entry.game_date == date(2019, 1, 15)


def test_audit_query_excludes_before_window(db):
    """Entries before 2018-12-20 should not appear in the audit query."""
    from scripts.audit_2018_19_restoration import _build_2018_19_query

    _seed_base(db)
    _seed_entry(db, id=610, row_number=101, game_date=date(2018, 12, 19), matchup="LAL@GSW")

    rows = _build_2018_19_query(db).all()
    assert len(rows) == 0


def test_audit_query_excludes_after_window(db):
    """Entries on or after 2019-10-22 (current cutoff) should not appear."""
    from scripts.audit_2018_19_restoration import _build_2018_19_query

    _seed_base(db)
    _seed_entry(db, id=620, row_number=102, game_date=date(2019, 10, 22), matchup="LAL@GSW")

    rows = _build_2018_19_query(db).all()
    assert len(rows) == 0


def test_audit_query_includes_boundary_dates(db):
    """Both 2018-12-20 (start) and 2019-10-21 (end) should be included."""
    from scripts.audit_2018_19_restoration import _build_2018_19_query

    _seed_base(db)
    _seed_entry(db, id=630, row_number=103, game_date=date(2018, 12, 20), matchup="LAL@GSW")
    _seed_entry(db, id=631, row_number=104, game_date=date(2019, 10, 21), matchup="LAL@BOS")

    rows = _build_2018_19_query(db).all()
    dates = sorted(r[0].game_date for r in rows)
    assert dates == [date(2018, 12, 20), date(2019, 10, 21)]


def test_audit_query_uses_same_projection_as_publisher(db):
    """The audit query should apply the same joins as publish_public_entries."""
    from scripts.audit_2018_19_restoration import _build_2018_19_query

    _seed_base(db)
    _seed_schedule(db, date(2019, 3, 1), "LAL@BOS", "2018-19", "regular")
    _seed_entry(db, id=640, row_number=105, game_date=date(2019, 3, 1), matchup="LAL@BOS")

    rows = _build_2018_19_query(db).all()
    assert len(rows) == 1
    r = rows[0]
    # Positional access: [0]=entry, [1]=player_name, [2]=team_name,
    # [3]=body_part, [4]=injury_type, [5]=source_url, [6]=report_date,
    # [7]=report_time, [8]=season, [9]=season_type
    assert r[1] == "LeBron James"  # canonical player name
    assert r[2] == "Los Angeles Lakers"  # canonical team name
    assert r[3] == "Knee"  # body_part from condition_index=1
    assert r[4] == "Soreness"  # injury_type from condition_index=1
    assert r[5] == "https://example.com/report.pdf"  # source_url
    assert r[8] == "2018-19"  # season from schedule
    assert r[9] == "regular"  # season_type from schedule


def test_audit_query_excludes_non_condition_index_1(db):
    """Entries without condition_index=1 should be excluded from audit query."""
    from scripts.audit_2018_19_restoration import _build_2018_19_query

    _seed_base(db)
    entry = NBAReportEntry(
        id=650,
        report_id=400,
        page_number=1,
        row_number=106,
        team_id=200,
        player_id=100,
        entry_type="player",
        game_date=date(2019, 5, 1),
        matchup="LAL@GSW",
        team_name_raw="LAL",
        player_name_raw="LeBron James",
        status="Out",
        raw_reason="back tightness",
        raw_row_text="raw",
    )
    db.add(entry)
    db.flush()
    # Only add condition_index=2, no index=1
    cond = NBAInjuryCondition(
        report_entry_id=entry.id,
        condition_index=2,
        body_part="Back",
        injury_type="Tightness",
        normalized_reason="back tightness",
        classification_version="v1",
        is_injury=True,
    )
    db.add(cond)
    db.flush()

    rows = _build_2018_19_query(db).all()
    assert len(rows) == 0


def test_audit_no_off_season_gap():
    """Verify no NBAReportEntry rows exist between 2019-10-01 and 2019-10-21.

    This is the critical safety check: changing the cutoff should not pull in
    unintended rows because the NBA season has an off-season gap.
    """
    from sqlalchemy import create_engine

    from app.db.base import Base

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    from sqlalchemy.orm import Session

    with Session(engine) as session:
        # Seed a player, team, candidate, report
        player = NBAPlayer(id=100, canonical_name="Test Player", name_key="testplayer")
        team = NBATeam(id=200, canonical_name="Test Team", abbreviation="TST")
        candidate = NBAReportCandidate(
            id=300, source_url="https://example.com/r.pdf",
            report_date=date(2019, 10, 5), status="parsed",
        )
        report = NBAReport(
            id=400, candidate_id=300, report_date=date(2019, 10, 5),
            report_time=time(17, 0), source_url="https://example.com/r.pdf",
            content_hash="abc", content=b"x", content_type="application/pdf",
            byte_length=1, parse_status="parsed",
        )
        session.add_all([player, team, candidate, report])
        session.flush()

        # Seed an entry at 2019-10-18 (inside the off-season gap)
        entry = NBAReportEntry(
            id=500, report_id=400, page_number=1, row_number=1,
            team_id=200, player_id=100, entry_type="player",
            game_date=date(2019, 10, 18), matchup="TST@TST",
            team_name_raw="TST", player_name_raw="Test Player",
            status="Out", raw_reason="knee soreness", raw_row_text="raw",
        )
        session.add(entry)
        cond = NBAInjuryCondition(
            report_entry_id=entry.id, condition_index=1,
            body_part="Knee", injury_type="Soreness",
            normalized_reason="knee soreness", classification_version="v1",
            is_injury=True,
        )
        session.add(cond)
        session.flush()

        from scripts.audit_2018_19_restoration import _AUDIT_END, _CURRENT_CUTOFF

        off_season = (
            session.query(func.count(NBAReportEntry.id))
            .filter(
                NBAReportEntry.game_date > _AUDIT_END,
                NBAReportEntry.game_date < _CURRENT_CUTOFF,
            )
            .scalar()
        )
        assert off_season == 0


# ── 2018-19 partial restoration via --season 2018-19 ────────────────────────


def _seed_2018_19_base(session: Session):
    """Insert shared player, team, candidate, report for 2018-19 tests."""
    player = NBAPlayer(id=100, canonical_name="LeBron James", name_key="lebron james")
    team = NBATeam(id=200, canonical_name="Los Angeles Lakers", abbreviation="LAL")
    candidate = NBAReportCandidate(
        id=300,
        source_url="https://example.com/report_2018_19.pdf",
        report_date=date(2019, 1, 15),
        status="parsed",
    )
    report = NBAReport(
        id=400,
        candidate_id=300,
        report_date=date(2019, 1, 15),
        report_time=time(17, 30),
        source_url="https://example.com/report_2018_19.pdf",
        content_hash="abc123",
        content=b"dummy",
        content_type="application/pdf",
        byte_length=5,
        parse_status="parsed",
    )
    session.add_all([player, team, candidate, report])
    session.flush()


def _seed_2018_19_entry(session: Session, **overrides):
    """Insert an NBAReportEntry with condition_index=1 in the 2018-19 window."""
    defaults = dict(
        id=500,
        report_id=400,
        page_number=1,
        row_number=1,
        team_id=200,
        player_id=100,
        entry_type="player",
        game_date=date(2019, 1, 15),
        game_time=time(19, 30),
        matchup="LAL@BOS",
        team_name_raw="LAL",
        player_name_raw="LeBron James",
        status="Out",
        reason_category="Injury",
        raw_reason="sore left knee",
        raw_row_text="raw row",
    )
    defaults.update(overrides)
    entry = NBAReportEntry(**defaults)
    session.add(entry)
    session.flush()
    cond = NBAInjuryCondition(
        report_entry_id=entry.id,
        condition_index=1,
        body_part="Knee",
        injury_type="Soreness",
        normalized_reason="sore left knee",
        classification_version="v1",
        is_injury=True,
    )
    session.add(cond)
    session.flush()
    return entry


def test_2018_19_valid_rows_are_included(db):
    """Rows with season=2018-19 in the partial window are included."""
    _seed_2018_19_base(db)
    _seed_schedule(db, date(2019, 1, 15), "LAL@BOS", "2018-19", "regular")
    _seed_2018_19_entry(db)

    result = publish_public_entries(
        db,
        min_date=date(2018, 12, 20),
        max_date=date(2019, 10, 21),
        season_filter="2018-19",
    )
    db.flush()

    assert result.inserted == 1
    pub = db.query(PublicInjuryEntry).one()
    assert pub.game_date == date(2019, 1, 15)
    assert pub.season == "2018-19"
    assert pub.season_type == "regular"


def test_2018_19_dates_before_start_are_excluded(db):
    """Rows with game_date before 2018-12-20 are excluded."""
    _seed_2018_19_base(db)
    _seed_schedule(db, date(2018, 12, 19), "LAL@BOS", "2018-19", "regular")
    _seed_2018_19_entry(db, id=510, row_number=10, game_date=date(2018, 12, 19))

    result = publish_public_entries(
        db,
        min_date=date(2018, 12, 20),
        max_date=date(2019, 10, 21),
        season_filter="2018-19",
    )
    db.flush()

    assert result.inserted == 0
    assert db.query(PublicInjuryEntry).count() == 0


def test_2018_19_dates_after_end_are_excluded(db):
    """Rows with game_date after 2019-10-21 are excluded."""
    _seed_2018_19_base(db)
    _seed_schedule(db, date(2019, 10, 22), "LAL@BOS", "2019-20", "regular")
    _seed_2018_19_entry(db, id=520, row_number=20, game_date=date(2019, 10, 22))

    result = publish_public_entries(
        db,
        min_date=date(2018, 12, 20),
        max_date=date(2019, 10, 21),
        season_filter="2018-19",
    )
    db.flush()

    assert result.inserted == 0
    assert db.query(PublicInjuryEntry).count() == 0


def test_2019_20_onward_behavior_unchanged(db):
    """Default behavior (no season_filter) still publishes 2019-20 onward."""
    _seed_base(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")
    _seed_entry(db)

    result = publish_public_entries(db)
    db.flush()

    assert result.inserted == 1
    pub = db.query(PublicInjuryEntry).one()
    assert pub.season == "2024-25"


def test_2018_19_non_matching_season_excluded(db):
    """Pre-2019-20 rows whose season != 2018-19 are excluded by season_filter."""
    _seed_2018_19_base(db)
    # This row has season=2019-20 (preseason), not 2018-19
    _seed_schedule(db, date(2019, 10, 1), "LAL@BOS", "2019-20", "preseason")
    _seed_2018_19_entry(db, id=530, row_number=30, game_date=date(2019, 10, 1))

    result = publish_public_entries(
        db,
        min_date=date(2018, 12, 20),
        max_date=date(2019, 10, 21),
        season_filter="2018-19",
    )
    db.flush()

    assert result.inserted == 0
    assert db.query(PublicInjuryEntry).count() == 0


def test_2018_19_null_season_excluded(db):
    """Pre-2019-20 rows with NULL season (no schedule match) are excluded."""
    _seed_2018_19_base(db)
    # No schedule game seeded, so season will be NULL
    _seed_2018_19_entry(db, id=540, row_number=40, game_date=date(2019, 3, 1))

    result = publish_public_entries(
        db,
        min_date=date(2018, 12, 20),
        max_date=date(2019, 10, 21),
        season_filter="2018-19",
    )
    db.flush()

    assert result.inserted == 0
    assert db.query(PublicInjuryEntry).count() == 0


def test_2018_19_boundary_dates_included(db):
    """Both 2018-12-20 (start) and 2019-10-21 (end) are included."""
    _seed_2018_19_base(db)
    _seed_schedule(db, date(2018, 12, 20), "LAL@BOS", "2018-19", "regular")
    _seed_2018_19_entry(db, id=550, row_number=50, game_date=date(2018, 12, 20))

    # Second entry for the end boundary (different source_url to avoid key conflict)
    player = NBAPlayer(id=101, canonical_name="Kyrie Irving", name_key="kyrie irving")
    team = db.query(NBATeam).first()
    candidate2 = NBAReportCandidate(
        id=301,
        source_url="https://example.com/report_2018_19_end.pdf",
        report_date=date(2019, 10, 21),
        status="parsed",
    )
    report2 = NBAReport(
        id=401,
        candidate_id=301,
        report_date=date(2019, 10, 21),
        report_time=time(17, 0),
        source_url="https://example.com/report_2018_19_end.pdf",
        content_hash="def456",
        content=b"dummy2",
        content_type="application/pdf",
        byte_length=5,
        parse_status="parsed",
    )
    db.add_all([player, candidate2, report2])
    db.flush()

    _seed_schedule(db, date(2019, 10, 21), "LAL@BOS", "2018-19", "regular")
    entry2 = NBAReportEntry(
        id=551,
        report_id=401,
        page_number=1,
        row_number=1,
        team_id=200,
        player_id=101,
        entry_type="player",
        game_date=date(2019, 10, 21),
        matchup="LAL@BOS",
        team_name_raw="LAL",
        player_name_raw="Kyrie Irving",
        status="Out",
        raw_reason="sore right knee",
        raw_row_text="raw row 2",
    )
    db.add(entry2)
    db.flush()
    cond2 = NBAInjuryCondition(
        report_entry_id=entry2.id,
        condition_index=1,
        body_part="Knee",
        injury_type="Soreness",
        normalized_reason="sore right knee",
        classification_version="v1",
        is_injury=True,
    )
    db.add(cond2)
    db.flush()

    result = publish_public_entries(
        db,
        min_date=date(2018, 12, 20),
        max_date=date(2019, 10, 21),
        season_filter="2018-19",
    )
    db.flush()

    assert result.inserted == 2
    dates = sorted(p.game_date for p in db.query(PublicInjuryEntry).all())
    assert dates == [date(2018, 12, 20), date(2019, 10, 21)]


def test_2018_19_idempotent(db):
    """Running 2018-19 restoration twice does not duplicate rows."""
    _seed_2018_19_base(db)
    _seed_schedule(db, date(2019, 1, 15), "LAL@BOS", "2018-19", "regular")
    _seed_2018_19_entry(db)

    r1 = publish_public_entries(
        db,
        min_date=date(2018, 12, 20),
        max_date=date(2019, 10, 21),
        season_filter="2018-19",
    )
    db.flush()
    assert r1.inserted == 1

    r2 = publish_public_entries(
        db,
        min_date=date(2018, 12, 20),
        max_date=date(2019, 10, 21),
        season_filter="2018-19",
    )
    db.flush()
    assert r2.inserted == 0
    assert r2.updated == 1

    assert db.query(PublicInjuryEntry).count() == 1


def test_2018_19_restoration_does_not_affect_2019_20_data(db):
    """Restoring 2018-19 does not modify or remove existing 2019-20+ rows."""
    # First, publish a 2019-20 row using default behavior
    _seed_base(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")
    _seed_entry(db, id=600, row_number=100)

    result_2019 = publish_public_entries(db)
    db.flush()
    assert result_2019.inserted == 1
    existing_pub = db.query(PublicInjuryEntry).one()
    existing_id = existing_pub.id

    # Now restore 2018-19 data using the same base data (player/team already exist)
    _seed_schedule(db, date(2019, 1, 15), "LAL@BOS", "2018-19", "regular")

    # Create a 2018-19 entry with a different report to avoid key conflicts
    candidate2 = NBAReportCandidate(
        id=310,
        source_url="https://example.com/report_2018_19_cross.pdf",
        report_date=date(2019, 1, 15),
        status="parsed",
    )
    report2 = NBAReport(
        id=410,
        candidate_id=310,
        report_date=date(2019, 1, 15),
        report_time=time(17, 30),
        source_url="https://example.com/report_2018_19_cross.pdf",
        content_hash="cross123",
        content=b"dummy",
        content_type="application/pdf",
        byte_length=5,
        parse_status="parsed",
    )
    db.add_all([candidate2, report2])
    db.flush()

    entry2018 = NBAReportEntry(
        id=700,
        report_id=410,
        page_number=1,
        row_number=200,
        team_id=200,
        player_id=100,
        entry_type="player",
        game_date=date(2019, 1, 15),
        matchup="LAL@BOS",
        team_name_raw="LAL",
        player_name_raw="LeBron James",
        status="Out",
        raw_reason="sore left knee",
        raw_row_text="raw row",
    )
    db.add(entry2018)
    db.flush()
    cond = NBAInjuryCondition(
        report_entry_id=entry2018.id,
        condition_index=1,
        body_part="Knee",
        injury_type="Soreness",
        normalized_reason="sore left knee",
        classification_version="v1",
        is_injury=True,
    )
    db.add(cond)
    db.flush()

    result_2018 = publish_public_entries(
        db,
        min_date=date(2018, 12, 20),
        max_date=date(2019, 10, 21),
        season_filter="2018-19",
    )
    db.flush()
    assert result_2018.inserted == 1

    # Both rows should exist
    assert db.query(PublicInjuryEntry).count() == 2
    # The 2019-20 row should be unchanged
    unchanged = db.query(PublicInjuryEntry).filter(PublicInjuryEntry.id == existing_id).one()
    assert unchanged.game_date == date(2025, 1, 15)
    assert unchanged.season == "2024-25"
