from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import NBAInjuryCondition, NBAInjuryEpisode, NBAReportEntry
from app.nba.classification import CLASSIFICATION_VERSION, classify_conditions


@dataclass(frozen=True)
class ReclassificationResult:
    selected: int
    updated: int


def reclassify_conditions(session: Session) -> ReclassificationResult:
    if session.scalar(select(func.count()).select_from(NBAInjuryEpisode)):
        raise RuntimeError(
            "Reclassification would invalidate derived episodes; rebuild from a cleared derived "
            "episode layer in the same controlled workflow"
        )
    selected = (
        session.scalar(
            select(func.count())
            .select_from(NBAInjuryCondition)
            .where(NBAInjuryCondition.classification_version != CLASSIFICATION_VERSION)
        )
        or 0
    )
    updated = 0
    while True:
        entries = list(
            session.scalars(
                select(NBAReportEntry)
                .join(
                    NBAInjuryCondition,
                    NBAInjuryCondition.report_entry_id == NBAReportEntry.id,
                )
                .where(NBAInjuryCondition.classification_version != CLASSIFICATION_VERSION)
                .distinct()
                .order_by(NBAReportEntry.id)
                .limit(5_000)
            )
        )
        if not entries:
            break
        existing_by_entry: dict[int, list[NBAInjuryCondition]] = defaultdict(list)
        for condition in session.scalars(
            select(NBAInjuryCondition)
            .where(NBAInjuryCondition.report_entry_id.in_([entry.id for entry in entries]))
            .order_by(
                NBAInjuryCondition.report_entry_id,
                NBAInjuryCondition.condition_index,
            )
        ):
            existing_by_entry[condition.report_entry_id].append(condition)
        for entry in entries:
            existing = existing_by_entry[entry.id]
            classifications = classify_conditions(entry.raw_reason, entry.reason_category)
            for condition_index, classified in enumerate(classifications, start=1):
                condition = (
                    existing[condition_index - 1]
                    if condition_index <= len(existing)
                    else NBAInjuryCondition(
                        report_entry_id=entry.id, condition_index=condition_index
                    )
                )
                condition.condition_index = condition_index
                condition.body_part = classified.body_part
                condition.laterality = classified.laterality
                condition.injury_type = classified.injury_type
                condition.normalized_reason = classified.normalized_reason
                condition.classification_version = classified.classification_version
                condition.is_injury = classified.is_injury
                if condition_index > len(existing):
                    session.add(condition)
                updated += 1
            for obsolete in existing[len(classifications) :]:
                session.delete(obsolete)
        session.commit()
    return ReclassificationResult(selected=selected, updated=updated)


def classify_unprocessed_entries(session: Session) -> ReclassificationResult:
    unprocessed_ids = (
        session.scalars(
            select(NBAReportEntry.id)
            .outerjoin(
                NBAInjuryCondition,
                NBAInjuryCondition.report_entry_id == NBAReportEntry.id,
            )
            .where(NBAInjuryCondition.id.is_(None))
            .order_by(NBAReportEntry.id)
        )
    ).all()
    selected = len(unprocessed_ids)
    updated = 0
    batch_size = 5_000
    for offset in range(0, selected, batch_size):
        batch_ids = unprocessed_ids[offset : offset + batch_size]
        entries = list(
            session.scalars(
                select(NBAReportEntry)
                .where(NBAReportEntry.id.in_(batch_ids))
                .order_by(NBAReportEntry.id)
            )
        )
        for entry in entries:
            classifications = classify_conditions(entry.raw_reason, entry.reason_category)
            for condition_index, classified in enumerate(classifications, start=1):
                condition = NBAInjuryCondition(
                    report_entry_id=entry.id, condition_index=condition_index
                )
                condition.body_part = classified.body_part
                condition.laterality = classified.laterality
                condition.injury_type = classified.injury_type
                condition.normalized_reason = classified.normalized_reason
                condition.classification_version = classified.classification_version
                condition.is_injury = classified.is_injury
                session.add(condition)
                updated += 1
        session.commit()
    return ReclassificationResult(selected=selected, updated=updated)
