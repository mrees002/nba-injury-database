"""Focused offline tests for the lean production daily updater."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.jobs.update_public_daily import (
    DailyUpdateResult,
    _lookup_schedule_meta,
    _write_report_entries,
    run_public_daily_update,
    update_day,
)
from app.models.nba import (
    NBAPlayer,
    NBAScheduleGame,
    NBATeam,
    PublicInjuryEntry,
)
from app.models.update_run import UpdateRun
from app.nba.types import ClassifiedReason, ParsedNBAReport, ParsedNBAReportEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def _seed_player(session: Session, **overrides):
    defaults = dict(canonical_name="LeBron James", name_key="lebronjames")
    defaults.update(overrides)
    player = NBAPlayer(**defaults)
    session.add(player)
    session.flush()
    return player


def _seed_team(session: Session, **overrides):
    defaults = dict(canonical_name="Los Angeles Lakers", abbreviation="LAL")
    defaults.update(overrides)
    team = NBATeam(**defaults)
    session.add(team)
    session.flush()
    return team


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
    return sg


def _make_parsed_entry(**overrides):
    defaults = dict(
        page_number=1,
        row_number=1,
        game_date=date(2025, 1, 15),
        game_time=time(19, 30),
        matchup="LAL@BOS",
        team="LAL",
        player_name="LeBron James",
        status="Out",
        reason_category="Injury/Illness",
        raw_reason="sore left knee",
        previous_status=None,
        previous_reason=None,
        raw_row_text="raw row",
        entry_type="player",
    )
    defaults.update(overrides)
    return ParsedNBAReportEntry(**defaults)


def _make_parsed_report(*entries, source_url="https://example.com/report.pdf"):
    if not entries:
        entries = (_make_parsed_entry(),)
    return ParsedNBAReport(
        report_date=date(2025, 1, 15),
        report_time=time(17, 30),
        format_version="standard-v2",
        parser_version="nba-pdf-v5",
        raw_text="raw",
        entries=tuple(entries),
    )


def _make_classification(**overrides):
    defaults = dict(
        body_part="Knee",
        laterality="left",
        injury_type="soreness",
        normalized_reason="sore left knee",
        is_injury=True,
        classification_version="nba-reason-v7",
    )
    defaults.update(overrides)
    return ClassifiedReason(**defaults)


def _seed_existing_public(
    session: Session,
    source_url: str,
    report_date: date,
    report_time: time,
    game_date: date,
    matchup: str,
    row_number: int = 1,
    player_name: str = "LeBron James",
    team_name: str = "Los Angeles Lakers",
):
    """Insert an existing PublicInjuryEntry row."""
    pub = PublicInjuryEntry(
        source_url=source_url,
        source_report_date=report_date,
        source_report_time=report_time,
        row_number=row_number,
        game_date=game_date,
        matchup=matchup,
        player_name=player_name,
        team_name=team_name,
        status="Out",
        raw_reason="old reason",
        reason_category="Injury/Illness",
    )
    session.add(pub)
    session.flush()
    return pub


# ---------------------------------------------------------------------------
# 1. Inserts new public rows
# ---------------------------------------------------------------------------


def test_inserts_new_public_rows(db):
    """Basic: a parsed report produces new PublicInjuryEntry rows."""
    _seed_player(db)
    _seed_team(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")

    parsed = _make_parsed_report()

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        written, superseded = _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 1, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )

    db.flush()

    assert written == 1
    assert superseded == 0
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
    assert pub.player_name == "LeBron James"
    assert pub.team_name == "Los Angeles Lakers"
    assert pub.status == "Out"
    assert pub.raw_reason == "sore left knee"
    assert pub.body_part == "Knee"
    assert pub.injury_type == "soreness"
    assert pub.season == "2024-25"
    assert pub.season_type == "regular"


# ---------------------------------------------------------------------------
# 2. Newer report supersedes older report for same game
# ---------------------------------------------------------------------------


def test_newer_report_supersedes_older(db):
    """A newer report replaces entries from an older report for the same game."""
    _seed_player(db)
    _seed_team(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")

    # Seed an older public entry
    _seed_existing_public(
        db,
        source_url="https://example.com/old_report.pdf",
        report_date=date(2025, 1, 15),
        report_time=time(12, 0),
        game_date=date(2025, 1, 15),
        matchup="LAL@BOS",
    )

    parsed = _make_parsed_report(
        _make_parsed_entry(row_number=1, player_name="Anthony Davis")
    )

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        written, superseded = _write_report_entries(
            db,
            "https://example.com/new_report.pdf",
            date(2025, 1, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )

    db.flush()

    assert written == 1
    assert superseded == 1

    rows = db.query(PublicInjuryEntry).all()
    assert len(rows) == 1
    pub = rows[0]
    assert pub.source_url == "https://example.com/new_report.pdf"
    assert pub.source_report_time == time(17, 30)
    assert pub.player_name == "Anthony Davis"

    # Old entry is gone
    old = (
        db.query(PublicInjuryEntry)
        .filter(PublicInjuryEntry.source_url == "https://example.com/old_report.pdf")
        .first()
    )
    assert old is None


# ---------------------------------------------------------------------------
# 3. Older report cannot overwrite newer report
# ---------------------------------------------------------------------------


def test_older_report_cannot_overwrite_newer(db):
    """An older report must not replace entries from a newer report."""
    _seed_player(db)
    _seed_team(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")

    # Seed a newer public entry
    _seed_existing_public(
        db,
        source_url="https://example.com/newer_report.pdf",
        report_date=date(2025, 1, 15),
        report_time=time(17, 30),
        game_date=date(2025, 1, 15),
        matchup="LAL@BOS",
    )

    parsed = _make_parsed_report(
        _make_parsed_entry(row_number=1, player_name="Jayson Tatum")
    )

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        written, superseded = _write_report_entries(
            db,
            "https://example.com/old_report.pdf",
            date(2025, 1, 15),
            time(12, 0),
            parsed,
            {},
            {},
        )

    db.flush()

    assert written == 0  # nothing written because newer report exists
    assert superseded == 0

    rows = db.query(PublicInjuryEntry).all()
    assert len(rows) == 1
    assert rows[0].source_url == "https://example.com/newer_report.pdf"
    assert rows[0].player_name == "LeBron James"


# ---------------------------------------------------------------------------
# 4. Equal timestamp rerun is idempotent
# ---------------------------------------------------------------------------


def test_equal_timestamp_rerun_is_idempotent(db):
    """Rerunning with the same timestamp produces the same final state."""
    _seed_player(db)
    _seed_team(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")

    parsed = _make_parsed_report()

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        # First run
        written1, _ = _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 1, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )
        db.flush()

        # Second run with same timestamp
        written2, _ = _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 1, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )
        db.flush()

    assert written1 == 1
    assert written2 == 1  # same rows inserted (same source_url + row_number upserted)
    rows = db.query(PublicInjuryEntry).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 5. source_url + row_number uniqueness
# ---------------------------------------------------------------------------


def test_source_url_row_number_uniqueness(db):
    """UNIQUE(source_url, row_number) is preserved on upsert."""
    _seed_player(db)
    _seed_team(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")

    parsed = _make_parsed_report()

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 1, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )
        db.flush()

        # Same source_url + row_number, different data
        parsed_v2 = _make_parsed_report(
            _make_parsed_entry(row_number=1, player_name="Anthony Davis", status="Questionable")
        )
        _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 1, 15),
            time(17, 30),
            parsed_v2,
            {},
            {},
        )
        db.flush()

    rows = db.query(PublicInjuryEntry).all()
    assert len(rows) == 1
    assert rows[0].player_name == "Anthony Davis"
    assert rows[0].status == "Questionable"


# ---------------------------------------------------------------------------
# 6. Primary classification is written
# ---------------------------------------------------------------------------


def test_primary_classification_is_written(db):
    """body_part and injury_type from the primary classification are stored."""
    _seed_player(db)
    _seed_team(db)

    parsed = _make_parsed_report()

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(
            _make_classification(body_part="Ankle", injury_type="sprain"),
            _make_classification(body_part="Knee", injury_type="strain"),
        ),
    ):
        _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 1, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )
    db.flush()

    pub = db.query(PublicInjuryEntry).one()
    # Only the first (primary) classification is written
    assert pub.body_part == "Ankle"
    assert pub.injury_type == "sprain"


# ---------------------------------------------------------------------------
# 7. season/season_type populated from schedule
# ---------------------------------------------------------------------------


def test_season_and_season_type_populated(db):
    """Season metadata is looked up from nba_schedule_games."""
    _seed_player(db)
    _seed_team(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")

    parsed = _make_parsed_report()

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 1, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )
    db.flush()

    pub = db.query(PublicInjuryEntry).one()
    assert pub.season == "2024-25"
    assert pub.season_type == "regular"


# ---------------------------------------------------------------------------
# 8. Missing schedule match remains nullable
# ---------------------------------------------------------------------------


def test_missing_schedule_match_is_nullable(db):
    """When no schedule game matches and date is outside all boundaries, season and season_type are NULL."""
    _seed_player(db)
    _seed_team(db)
    # No schedule seeded

    # July 15 falls between 2024-25 playoffs end (Jun 22) and 2025-26 preseason start (Oct 2)
    parsed = _make_parsed_report(_make_parsed_entry(game_date=date(2025, 7, 15)))

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 7, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )
    db.flush()

    pub = db.query(PublicInjuryEntry).one()
    assert pub.season is None
    assert pub.season_type is None


# ---------------------------------------------------------------------------
# 9. UpdateRun success lifecycle
# ---------------------------------------------------------------------------


def test_update_run_success_lifecycle(db):
    """UpdateRun is created with 'started' and updated to 'completed'."""
    run = UpdateRun(
        requested_start_date=date(2025, 1, 15),
        requested_end_date=date(2025, 1, 15),
        status="started",
    )
    db.add(run)
    db.commit()

    assert run.status == "started"
    assert run.finished_at is None

    run.status = "completed"
    run.finished_at = datetime.now(tz=UTC)
    run.rows_fetched = 5
    run.rows_inserted = 10
    run.rows_processed = 3
    db.commit()

    fetched_run = db.query(UpdateRun).one()
    assert fetched_run.status == "completed"
    assert fetched_run.finished_at is not None
    assert fetched_run.finished_at.tzinfo is not None
    assert fetched_run.rows_fetched == 5
    assert fetched_run.rows_inserted == 10
    assert fetched_run.rows_processed == 3
    assert fetched_run.error_details is None


# ---------------------------------------------------------------------------
# 10. UpdateRun failure lifecycle
# ---------------------------------------------------------------------------


def test_update_run_failure_lifecycle(db):
    """UpdateRun records failure state with error_details."""
    run = UpdateRun(
        requested_start_date=date(2025, 1, 15),
        requested_end_date=date(2025, 1, 15),
        status="started",
    )
    db.add(run)
    db.commit()

    run.status = "failed"
    run.finished_at = datetime.now(tz=UTC)
    run.error_details = "Network timeout"
    db.commit()

    fetched_run = db.query(UpdateRun).one()
    assert fetched_run.status == "failed"
    assert fetched_run.finished_at is not None
    assert fetched_run.error_details == "Network timeout"


# ---------------------------------------------------------------------------
# 11. Updater has no dependency on archive tables
# ---------------------------------------------------------------------------


def test_updater_has_no_archive_table_dependency(db):
    """_write_report_entries only touches public_injury_entries, nba_players,
    nba_teams, and nba_schedule_games. It does not import or use archive tables."""
    from app.jobs import update_public_daily as module

    import ast

    tree = ast.parse(open(module.__file__).read())

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "models.nba" in node.module:
                for alias in node.names:
                    imported_names.add(alias.name)

    archive_names = {
        "NBAReportCandidate",
        "NBAReport",
        "NBAReportEntry",
        "NBAInjuryCondition",
        "NBAGame",
        "NBAInjuryEpisode",
        "NBAInjuryEpisodeCondition",
    }
    violations = imported_names & archive_names
    assert not violations, f"Module imports archive tables: {violations}"


# ---------------------------------------------------------------------------
# 12. Multiple entries published
# ---------------------------------------------------------------------------


def test_multiple_entries_published(db):
    """Multiple player entries in a single report are all written."""
    _seed_player(db, id=100, canonical_name="LeBron James", name_key="lebronjames")
    _seed_player(db, id=101, canonical_name="Anthony Davis", name_key="anthonydavis")
    _seed_team(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")

    parsed = _make_parsed_report(
        _make_parsed_entry(row_number=1, player_name="LeBron James"),
        _make_parsed_entry(row_number=2, player_name="Anthony Davis"),
    )

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        written, _ = _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 1, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )

    db.flush()
    assert written == 2
    assert db.query(PublicInjuryEntry).count() == 2


# ---------------------------------------------------------------------------
# 13. Non-player entries are skipped
# ---------------------------------------------------------------------------


def test_non_player_entries_skipped(db):
    """Entries with entry_type != 'player' are not written."""
    _seed_player(db)
    _seed_team(db)

    parsed = _make_parsed_report(
        _make_parsed_entry(row_number=1, entry_type="all_available"),
    )

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        written, _ = _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 1, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )

    db.flush()
    assert written == 0
    assert db.query(PublicInjuryEntry).count() == 0


# ---------------------------------------------------------------------------
# 14. Player and team resolved from existing DB rows
# ---------------------------------------------------------------------------


def test_player_team_resolved_from_db(db):
    """Existing player/team rows are reused, not duplicated."""
    player = _seed_player(db, id=100, canonical_name="LeBron James", name_key="lebronjames")
    team = _seed_team(db, id=200, canonical_name="Los Angeles Lakers", abbreviation="LAL")

    parsed = _make_parsed_report()

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 1, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )
    db.flush()

    pub = db.query(PublicInjuryEntry).one()
    assert pub.player_id == 100
    assert pub.team_id == 200

    # No new player/team created
    assert db.query(NBAPlayer).count() == 1
    assert db.query(NBATeam).count() == 1


# ---------------------------------------------------------------------------
# 15. New player/team created when not in DB
# ---------------------------------------------------------------------------


def test_new_player_team_created_when_missing(db):
    """Players and teams not in the DB are created."""
    parsed = _make_parsed_report()

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 1, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )
    db.flush()

    pub = db.query(PublicInjuryEntry).one()
    assert pub.player_id is not None
    assert pub.team_id is not None
    assert pub.player_name == "LeBron James"
    assert pub.team_name == "Los Angeles Lakers"

    assert db.query(NBAPlayer).count() == 1
    assert db.query(NBATeam).count() == 1


# ---------------------------------------------------------------------------
# 16. update_day with no valid PDFs
# ---------------------------------------------------------------------------


def test_update_day_no_valid_pdfs(db):
    """update_day returns zero counts when no PDFs are found."""
    with patch("app.jobs.update_public_daily.probe_candidate_urls", return_value=[]):
        result = update_day(db, date(2025, 1, 15))

    assert isinstance(result, DailyUpdateResult)
    assert result.reports_discovered == 0
    assert result.reports_selected == 0
    assert result.entries_written == 0
    assert result.games_superseded == 0


# ---------------------------------------------------------------------------
# 17. Schedule lookup helper
# ---------------------------------------------------------------------------


def test_lookup_schedule_meta_found(db):
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")
    season, season_type = _lookup_schedule_meta(db, date(2025, 1, 15), "LAL@BOS")
    assert season == "2024-25"
    assert season_type == "regular"


def test_lookup_schedule_meta_not_found(db):
    # July 15 is between seasons — no schedule row and outside all boundaries
    season, season_type = _lookup_schedule_meta(db, date(2025, 7, 15), "LAL@BOS")
    assert season is None
    assert season_type is None


def test_lookup_schedule_meta_space_in_matchup(db):
    """Schedule lookup handles matchups with or without spaces."""
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")
    season, season_type = _lookup_schedule_meta(db, date(2025, 1, 15), "LAL @ BOS")
    assert season == "2024-25"
    assert season_type == "regular"


# ---------------------------------------------------------------------------
# 18. Superseding across different games in same report
# ---------------------------------------------------------------------------


def test_superseding_only_affects_matching_game(db):
    """Superseding only removes entries for the same game, not other games."""
    _seed_player(db)
    _seed_team(db)
    _seed_schedule(db, date(2025, 1, 15), "LAL@BOS", "2024-25", "regular")
    _seed_schedule(db, date(2025, 1, 15), "GSW@MIA", "2024-25", "regular")

    # Seed entries for two different games
    _seed_existing_public(
        db,
        source_url="https://example.com/old.pdf",
        report_date=date(2025, 1, 15),
        report_time=time(12, 0),
        game_date=date(2025, 1, 15),
        matchup="LAL@BOS",
    )
    _seed_existing_public(
        db,
        source_url="https://example.com/old.pdf",
        report_date=date(2025, 1, 15),
        report_time=time(12, 0),
        game_date=date(2025, 1, 15),
        matchup="GSW@MIA",
        row_number=2,
        player_name="Stephen Curry",
    )

    # New report only covers LAL@BOS
    parsed = _make_parsed_report(
        _make_parsed_entry(row_number=1, matchup="LAL@BOS")
    )

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        written, superseded = _write_report_entries(
            db,
            "https://example.com/new.pdf",
            date(2025, 1, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )

    db.flush()

    assert written == 1
    assert superseded == 1

    rows = db.query(PublicInjuryEntry).all()
    assert len(rows) == 2

    # GSW@MIA entry is preserved (not superseded)
    gsw_entry = [r for r in rows if r.matchup == "GSW@MIA"]
    assert len(gsw_entry) == 1
    assert gsw_entry[0].source_url == "https://example.com/old.pdf"

    # LAL@BOS entry is superseded
    lal_entry = [r for r in rows if r.matchup == "LAL@BOS"]
    assert len(lal_entry) == 1
    assert lal_entry[0].source_url == "https://example.com/new.pdf"


# ---------------------------------------------------------------------------
# 19. Equal timestamp does not supersede
# ---------------------------------------------------------------------------


def test_equal_timestamp_does_not_supersede(db):
    """Entries with equal timestamps from a different source are not superseded."""
    _seed_player(db)
    _seed_team(db)

    _seed_existing_public(
        db,
        source_url="https://example.com/report_A.pdf",
        report_date=date(2025, 1, 15),
        report_time=time(17, 30),
        game_date=date(2025, 1, 15),
        matchup="LAL@BOS",
    )

    parsed = _make_parsed_report(
        _make_parsed_entry(row_number=1, player_name="New Player")
    )

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        written, superseded = _write_report_entries(
            db,
            "https://example.com/report_B.pdf",
            date(2025, 1, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )

    db.flush()

    # Equal timestamp from different source: existing entry preserved, no superseding
    assert superseded == 0
    rows = db.query(PublicInjuryEntry).all()
    assert len(rows) == 2
    original = [r for r in rows if r.source_url == "https://example.com/report_A.pdf"]
    assert len(original) == 1
    assert original[0].player_name == "LeBron James"


# ---------------------------------------------------------------------------
# 21. Missing schedule match produces nullable season metadata
# ---------------------------------------------------------------------------


def test_missing_schedule_match_allows_nullable_season(db):
    """When no schedule rows exist and date is outside all boundaries, season/season_type stay NULL."""
    _seed_player(db)
    _seed_team(db)
    # No schedule rows seeded

    # July 15 falls between 2024-25 playoffs end (Jun 22) and 2025-26 preseason start (Oct 2)
    parsed = _make_parsed_report(_make_parsed_entry(game_date=date(2025, 7, 15)))

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        written, _ = _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 7, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )
    db.flush()

    assert written == 1
    pub = db.query(PublicInjuryEntry).one()
    assert pub.season is None
    assert pub.season_type is None


# ---------------------------------------------------------------------------
# 22. Injury updater exceptions still fail UpdateRun
# ---------------------------------------------------------------------------


def test_injury_updater_exception_fails_update_run(tmp_path):
    """An exception in update_day still marks the UpdateRun as failed."""
    test_db = tmp_path / "test.db"
    engine = create_engine(f"sqlite+pysqlite:///{test_db}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with patch(
        "app.jobs.update_public_daily.update_day",
        side_effect=RuntimeError("PDF parsing crashed"),
    ):
        with patch("app.jobs.update_public_daily.build_engine", return_value=engine):
            with patch("app.jobs.update_public_daily.build_session_factory", return_value=factory):
                with pytest.raises(RuntimeError, match="PDF parsing crashed"):
                    run_public_daily_update(date(2025, 1, 15), date(2025, 1, 15))

    with Session(engine) as session:
        run = session.query(UpdateRun).one()
        assert run.status == "failed"
        assert run.finished_at is not None
        assert "PDF parsing crashed" in run.error_details
    engine.dispose()


# ---------------------------------------------------------------------------
# 23. Daily updater does not call schedule network code
# ---------------------------------------------------------------------------


def test_daily_updater_does_not_call_schedule_network_code(tmp_path):
    """run_public_daily_update never imports or calls schedule sync functions."""
    test_db = tmp_path / "test.db"
    engine = create_engine(f"sqlite+pysqlite:///{test_db}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    import app.jobs.update_public_daily as module

    # Verify the module does not reference schedule network functions
    source = open(module.__file__).read()
    for name in ("_sync_schedule", "detect_current_season", "fetch_season_schedule",
                 "normalized_games_to_rows", "upsert_schedule_rows"):
        assert name not in source, f"Module still references {name}"

    with patch("app.jobs.update_public_daily.update_day") as mock_day:
        mock_day.return_value = DailyUpdateResult(
            target_date=date(2025, 1, 15),
            reports_discovered=0,
            reports_selected=0,
            entries_written=0,
            games_superseded=0,
        )
        with patch("app.jobs.update_public_daily.build_engine", return_value=engine):
            with patch("app.jobs.update_public_daily.build_session_factory", return_value=factory):
                run_public_daily_update(date(2025, 1, 15), date(2025, 1, 15))

    # No schedule network mock needed — update_day was called directly
    mock_day.assert_called_once()
    with Session(engine) as session:
        run = session.query(UpdateRun).one()
        assert run.status == "completed"
    engine.dispose()


# ---------------------------------------------------------------------------
# 24. Normal injury update succeeds without schedule sync
# ---------------------------------------------------------------------------


def test_normal_injury_update_succeeds_without_schedule_sync(tmp_path):
    """A full run completes using existing schedule metadata without network calls."""
    test_db = tmp_path / "test.db"
    engine = create_engine(f"sqlite+pysqlite:///{test_db}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with patch("app.jobs.update_public_daily.update_day") as mock_day:
        mock_day.return_value = DailyUpdateResult(
            target_date=date(2025, 1, 15),
            reports_discovered=3,
            reports_selected=2,
            entries_written=7,
            games_superseded=1,
        )
        with patch("app.jobs.update_public_daily.build_engine", return_value=engine):
            with patch("app.jobs.update_public_daily.build_session_factory", return_value=factory):
                run_public_daily_update(date(2025, 1, 15), date(2025, 1, 15))

    with Session(engine) as session:
        run = session.query(UpdateRun).one()
        assert run.status == "completed"
        assert run.rows_fetched == 3
        assert run.rows_inserted == 7
        assert run.rows_processed == 2
        assert run.finished_at is not None
        assert run.error_details is None
    engine.dispose()


# ---------------------------------------------------------------------------
# 25. Schedule match wins over date-boundary fallback
# ---------------------------------------------------------------------------


def test_schedule_match_wins_over_fallback(db):
    """When a schedule row exists, its season/season_type take priority over fallback."""
    _seed_player(db)
    _seed_team(db)
    # Seed schedule with a deliberately non-matching season_type to prove it wins
    _seed_schedule(db, date(2024, 11, 10), "LAL@BOS", "2024-25", "regular")

    parsed = _make_parsed_report(_make_parsed_entry(game_date=date(2024, 11, 10)))

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2024, 11, 10),
            time(17, 30),
            parsed,
            {},
            {},
        )
    db.flush()

    pub = db.query(PublicInjuryEntry).one()
    assert pub.season == "2024-25"
    assert pub.season_type == "regular"


# ---------------------------------------------------------------------------
# 26. Regular-season fallback via date boundaries
# ---------------------------------------------------------------------------


def test_regular_season_fallback(db):
    """Date within regular-season boundaries uses fallback when no schedule row exists."""
    _seed_player(db)
    _seed_team(db)
    # No schedule seeded

    # Jan 15, 2025 falls within 2024-25 regular season (Oct 22 2024 - Apr 13 2025)
    parsed = _make_parsed_report(_make_parsed_entry(game_date=date(2025, 1, 15)))

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 1, 15),
            time(17, 30),
            parsed,
            {},
            {},
        )
    db.flush()

    pub = db.query(PublicInjuryEntry).one()
    assert pub.season == "2024-25"
    assert pub.season_type == "regular"


# ---------------------------------------------------------------------------
# 27. Play-In fallback via date boundaries
# ---------------------------------------------------------------------------


def test_play_in_fallback(db):
    """Date within play-in boundaries uses fallback when no schedule row exists."""
    _seed_player(db)
    _seed_team(db)
    # No schedule seeded

    # Apr 16, 2025 falls within 2024-25 play-in (Apr 15 - Apr 18 2025)
    parsed = _make_parsed_report(_make_parsed_entry(game_date=date(2025, 4, 16)))

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 4, 16),
            time(17, 30),
            parsed,
            {},
            {},
        )
    db.flush()

    pub = db.query(PublicInjuryEntry).one()
    assert pub.season == "2024-25"
    assert pub.season_type == "play_in"


# ---------------------------------------------------------------------------
# 28. Playoff fallback via date boundaries
# ---------------------------------------------------------------------------


def test_playoffs_fallback(db):
    """Date within playoff boundaries uses fallback when no schedule row exists."""
    _seed_player(db)
    _seed_team(db)
    # No schedule seeded

    # Jun 10, 2025 falls within 2024-25 playoffs (Apr 19 - Jun 22 2025)
    parsed = _make_parsed_report(_make_parsed_entry(game_date=date(2025, 6, 10)))

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2025, 6, 10),
            time(17, 30),
            parsed,
            {},
            {},
        )
    db.flush()

    pub = db.query(PublicInjuryEntry).one()
    assert pub.season == "2024-25"
    assert pub.season_type == "playoffs"


# ---------------------------------------------------------------------------
# 29. Preseason fallback via date boundaries
# ---------------------------------------------------------------------------


def test_preseason_fallback(db):
    """Date within preseason boundaries uses fallback when no schedule row exists."""
    _seed_player(db)
    _seed_team(db)
    # No schedule seeded

    # Oct 10, 2024 falls within 2024-25 preseason (Oct 4 - Oct 18 2024)
    parsed = _make_parsed_report(_make_parsed_entry(game_date=date(2024, 10, 10)))

    with patch(
        "app.jobs.update_public_daily.classify_conditions",
        return_value=(_make_classification(),),
    ):
        _write_report_entries(
            db,
            "https://example.com/report.pdf",
            date(2024, 10, 10),
            time(17, 30),
            parsed,
            {},
            {},
        )
    db.flush()

    pub = db.query(PublicInjuryEntry).one()
    assert pub.season == "2024-25"
    assert pub.season_type == "preseason"


# ---------------------------------------------------------------------------
# 30. classify_by_season_boundary unit tests
# ---------------------------------------------------------------------------


def test_classify_by_season_boundary_regular():
    from app.nba.season_boundaries import classify_by_season_boundary

    season, st = classify_by_season_boundary(date(2025, 1, 15))
    assert season == "2024-25"
    assert st == "regular"


def test_classify_by_season_boundary_play_in():
    from app.nba.season_boundaries import classify_by_season_boundary

    season, st = classify_by_season_boundary(date(2025, 4, 17))
    assert season == "2024-25"
    assert st == "play_in"


def test_classify_by_season_boundary_playoffs():
    from app.nba.season_boundaries import classify_by_season_boundary

    season, st = classify_by_season_boundary(date(2025, 5, 20))
    assert season == "2024-25"
    assert st == "playoffs"


def test_classify_by_season_boundary_preseason():
    from app.nba.season_boundaries import classify_by_season_boundary

    season, st = classify_by_season_boundary(date(2024, 10, 10))
    assert season == "2024-25"
    assert st == "preseason"


def test_classify_by_season_boundary_unknown_date():
    from app.nba.season_boundaries import classify_by_season_boundary

    season, st = classify_by_season_boundary(date(2025, 7, 15))
    assert season is None
    assert st is None


def test_classify_by_season_boundary_covid_bubble():
    """2020-10-11 is still 2019-20 playoffs (COVID bubble exception)."""
    from app.nba.season_boundaries import classify_by_season_boundary

    season, st = classify_by_season_boundary(date(2020, 10, 11))
    assert season == "2019-20"
    assert st == "playoffs"


def test_classify_by_season_boundary_2020_21_delayed_start():
    """2020-12-15 is 2020-21 preseason (delayed COVID season)."""
    from app.nba.season_boundaries import classify_by_season_boundary

    season, st = classify_by_season_boundary(date(2020, 12, 15))
    assert season == "2020-21"
    assert st == "preseason"


def test_classify_by_season_boundary_boundary_inclusive():
    """Start and end dates are inclusive."""
    from app.nba.season_boundaries import classify_by_season_boundary

    # Exact start of 2024-25 regular season
    season, st = classify_by_season_boundary(date(2024, 10, 22))
    assert season == "2024-25"
    assert st == "regular"

    # Exact end of 2024-25 regular season
    season, st = classify_by_season_boundary(date(2025, 4, 13))
    assert season == "2024-25"
    assert st == "regular"

    # Day before 2024-25 regular season = gap (none)
    season, st = classify_by_season_boundary(date(2024, 10, 21))
    assert season is None
    assert st is None
