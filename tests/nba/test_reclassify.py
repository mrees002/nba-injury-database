from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import NBAInjuryCondition, NBAReportEntry
from app.nba.classification import CLASSIFICATION_VERSION
from app.nba.reclassify import reclassify_conditions


def test_reclassification_is_idempotent_and_does_not_mutate_raw_entry_lineage():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    raw_reason = "Injury/Illness - Left Groin; Soreness / G League Assignment"
    raw_row = "04/12/26 LAL@MIN Minnesota Timberwolves Example, Player Out " + raw_reason

    with Session(engine) as session:
        entry = NBAReportEntry(
            id=1,
            report_id=1,
            page_number=2,
            row_number=17,
            entry_type="player",
            game_date=date(2026, 4, 12),
            matchup="LAL@MIN",
            team_name_raw="Minnesota Timberwolves",
            player_name_raw="Example, Player",
            status="Out",
            reason_category="Injury/Illness",
            raw_reason=raw_reason,
            raw_row_text=raw_row,
        )
        session.add(entry)
        session.add(
            NBAInjuryCondition(
                report_entry_id=1,
                condition_index=1,
                normalized_reason="old",
                classification_version="nba-reason-v6",
                is_injury=False,
            )
        )
        session.commit()

        raw_snapshot = (
            entry.report_id,
            entry.page_number,
            entry.row_number,
            entry.raw_reason,
            entry.raw_row_text,
        )
        first = reclassify_conditions(session)
        conditions = list(
            session.scalars(select(NBAInjuryCondition).order_by(NBAInjuryCondition.condition_index))
        )

        assert (first.selected, first.updated) == (1, 2)
        assert [(item.body_part, item.injury_type, item.is_injury) for item in conditions] == [
            ("groin", "soreness", True),
            (None, None, False),
        ]
        assert {item.classification_version for item in conditions} == {CLASSIFICATION_VERSION}
        assert raw_snapshot == (
            entry.report_id,
            entry.page_number,
            entry.row_number,
            entry.raw_reason,
            entry.raw_row_text,
        )

        second = reclassify_conditions(session)
        assert (second.selected, second.updated) == (0, 0)
        assert len(list(session.scalars(select(NBAInjuryCondition)))) == 2
