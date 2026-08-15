from __future__ import annotations

import re
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import NBAInjuryCondition, NBAReportEntry
from app.nba.classification import _split_reason_parts, classify_conditions, classify_reason


def _reason_rows(counter: Counter[str | None], limit: int = 50) -> list[dict[str, object]]:
    return [
        {"reason": reason or "<null>", "observations": count}
        for reason, count in counter.most_common(limit)
    ]


def _potentially_ambiguous_unsplit(raw_reason: str | None, reason_category: str | None) -> bool:
    source = " ".join((raw_reason or "").split())
    detail = re.sub(r"^injury/illness\s*-\s*", "", source, flags=re.IGNORECASE)
    parts = _split_reason_parts(detail)
    if len(parts) < 2 or len(classify_conditions(raw_reason, reason_category)) > 1:
        return False
    classified_parts = [classify_reason(part, reason_category) for part in parts]
    return sum(part.body_part is not None for part in classified_parts) >= 2


def build_classification_audit(session: Session) -> dict[str, object]:
    total = session.scalar(select(func.count()).select_from(NBAInjuryCondition)) or 0
    injury = (
        session.scalar(
            select(func.count())
            .select_from(NBAInjuryCondition)
            .where(NBAInjuryCondition.is_injury.is_(True))
        )
        or 0
    )
    body = (
        session.scalar(
            select(func.count())
            .select_from(NBAInjuryCondition)
            .where(
                NBAInjuryCondition.is_injury.is_(True),
                NBAInjuryCondition.body_part.is_not(None),
            )
        )
        or 0
    )
    injury_type = (
        session.scalar(
            select(func.count())
            .select_from(NBAInjuryCondition)
            .where(
                NBAInjuryCondition.is_injury.is_(True),
                NBAInjuryCondition.injury_type.is_not(None),
            )
        )
        or 0
    )
    laterality = (
        session.scalar(
            select(func.count())
            .select_from(NBAInjuryCondition)
            .where(
                NBAInjuryCondition.is_injury.is_(True),
                NBAInjuryCondition.laterality.is_not(None),
            )
        )
        or 0
    )

    missing_body: Counter[str | None] = Counter()
    missing_type: Counter[str | None] = Counter()
    missing_both: Counter[str | None] = Counter()
    multi: Counter[str | None] = Counter()
    ambiguous: Counter[str | None] = Counter()
    bilateral: Counter[str | None] = Counter()
    entry_rows = session.execute(
        select(
            NBAReportEntry.raw_reason,
            NBAReportEntry.reason_category,
            func.count(),
        )
        .where(NBAReportEntry.player_id.is_not(None))
        .group_by(NBAReportEntry.raw_reason, NBAReportEntry.reason_category)
    )
    for raw_reason, reason_category, count in entry_rows:
        conditions = classify_conditions(raw_reason, reason_category)
        if len(conditions) > 1:
            multi[raw_reason] += count
        if _potentially_ambiguous_unsplit(raw_reason, reason_category):
            ambiguous[raw_reason] += count
        if any(condition.laterality == "bilateral" for condition in conditions):
            bilateral[raw_reason] += count
        for condition in conditions:
            if not condition.is_injury:
                continue
            if condition.body_part is None:
                missing_body[raw_reason] += count
            if condition.injury_type is None:
                missing_type[raw_reason] += count
            if condition.body_part is None and condition.injury_type is None:
                missing_both[raw_reason] += count

    def percentage(value: int) -> float:
        return round(value * 100 / injury, 3) if injury else 0

    return {
        "classified_conditions": total,
        "injury_conditions": injury,
        "non_injury_conditions": total - injury,
        "body_part_coverage_percentage": percentage(body),
        "injury_type_coverage_percentage": percentage(injury_type),
        "laterality_coverage_percentage": percentage(laterality),
        "missing_body_part_observations": sum(missing_body.values()),
        "missing_injury_type_observations": sum(missing_type.values()),
        "fully_unclassified_observations": sum(missing_both.values()),
        "most_common_missing_body_part_reasons": _reason_rows(missing_body),
        "most_common_missing_injury_type_reasons": _reason_rows(missing_type),
        "most_common_fully_unclassified_reasons": _reason_rows(missing_both),
        "multiple_condition_entries": sum(multi.values()),
        "most_common_multiple_condition_reasons": _reason_rows(multi),
        "potentially_ambiguous_unsplit_entries": sum(ambiguous.values()),
        "most_common_potentially_ambiguous_unsplit_reasons": _reason_rows(ambiguous),
        "bilateral_or_two_sided_entries": sum(bilateral.values()),
        "most_common_bilateral_or_two_sided_reasons": _reason_rows(bilateral),
    }
