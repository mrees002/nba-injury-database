from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from functools import cache
from hashlib import sha256
from io import StringIO
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Injury,
    NBAInjuryCondition,
    NBAInjuryEpisode,
    NBAInjuryEpisodeCondition,
    NBAPlayer,
    NBAReportEntry,
)
from app.nba.normalize import player_name_key
from app.nba.seasons import get_official_nba_season

BENCHMARK_VERSION = "nba-vs-pst-v1"
CONTINUOUS_NBA_COVERAGE_START = date(2019, 10, 22)
MAX_MATCH_DAYS = 7
DEFAULT_OUTPUT_DIRECTORY = Path("artifacts/validation")


@dataclass(frozen=True)
class BenchmarkEvent:
    source_id: int
    player_key: str
    player_name: str
    event_date: date
    body_part: str | None
    injury_type: str | None
    laterality: str | None
    reason: str
    source_parent_id: int | None = None
    lineage_count: int | None = None
    first_condition_id: int | None = None
    first_report_entry_id: int | None = None
    first_report_id: int | None = None


@dataclass(frozen=True)
class EventMatch:
    pst: BenchmarkEvent
    nba: BenchmarkEvent
    day_difference: int
    body_agreement: bool | None
    injury_type_agreement: bool | None
    laterality_agreement: bool | None
    reason_similarity: float
    pst_candidate_count: int
    nba_candidate_count: int

    @property
    def ambiguous(self) -> bool:
        return self.pst_candidate_count > 1 or self.nba_candidate_count > 1


@dataclass(frozen=True)
class BenchmarkResult:
    summary: dict[str, object]
    matches: list[dict[str, object]]
    nba_only: list[dict[str, object]]
    pst_only: list[dict[str, object]]
    discrepancies: list[dict[str, object]]


def _normalize_reason(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _extract_laterality(value: str | None) -> str | None:
    normalized = _normalize_reason(value)
    if re.search(r"\bbilateral\b|\bleft and right\b|\bright and left\b|\bboth\b", normalized):
        return "bilateral"
    has_left = bool(re.search(r"\bleft\b", normalized))
    has_right = bool(re.search(r"\bright\b", normalized))
    if has_left and has_right:
        return "bilateral"
    if has_left:
        return "left"
    if has_right:
        return "right"
    return None


def _body_family(value: str | None) -> str | None:
    return {
        "lower leg": "leg",
        "shin": "leg",
        "forearm": "arm",
    }.get(value or "", value)


def _agreement(left: str | None, right: str | None) -> bool | None:
    if left is None or right is None:
        return None
    return left == right


def _body_agreement(left: str | None, right: str | None) -> bool | None:
    if left is None or right is None:
        return None
    return _body_family(left) == _body_family(right)


def _reason_similarity(left: BenchmarkEvent, right: BenchmarkEvent) -> float:
    return round(SequenceMatcher(None, left.reason, right.reason).ratio(), 6)


def _pair_quality(left: BenchmarkEvent, right: BenchmarkEvent) -> tuple[int, int, int, int, int]:
    """Return costs after pair count; lower is better except reason similarity."""

    body = _body_agreement(left.body_part, right.body_part)
    injury_type = _agreement(left.injury_type, right.injury_type)
    laterality = _agreement(left.laterality, right.laterality)
    return (
        abs((left.event_date - right.event_date).days),
        int(body is False),
        int(injury_type is False),
        int(laterality is False),
        round(_reason_similarity(left, right) * 1_000_000),
    )


def _optimal_player_matches(
    left: list[BenchmarkEvent], right: list[BenchmarkEvent]
) -> tuple[tuple[int, int], ...]:
    """Find deterministic order-preserving, one-to-one matches for one player."""

    @cache
    def solve(
        left_index: int, right_index: int
    ) -> tuple[int, int, int, int, int, int, tuple[tuple[int, int], ...]]:
        if left_index == len(left) or right_index == len(right):
            return (0, 0, 0, 0, 0, 0, ())
        options = [solve(left_index + 1, right_index), solve(left_index, right_index + 1)]
        day_difference = abs((left[left_index].event_date - right[right_index].event_date).days)
        if day_difference <= MAX_MATCH_DAYS:
            day_cost, body_cost, type_cost, laterality_cost, reason_score = _pair_quality(
                left[left_index], right[right_index]
            )
            tail = solve(left_index + 1, right_index + 1)
            options.append(
                (
                    tail[0] + 1,
                    tail[1] + day_cost,
                    tail[2] + body_cost,
                    tail[3] + type_cost,
                    tail[4] + laterality_cost,
                    tail[5] + reason_score,
                    ((left_index, right_index),) + tail[6],
                )
            )
        return max(
            options,
            key=lambda item: (
                item[0],
                -item[1],
                -item[2],
                -item[3],
                -item[4],
                item[5],
                tuple((-left[i].source_id, -right[j].source_id) for i, j in item[6]),
            ),
        )

    return solve(0, 0)[6]


def _match_events(
    pst: list[BenchmarkEvent], nba: list[BenchmarkEvent]
) -> tuple[list[tuple[BenchmarkEvent, BenchmarkEvent]], list[BenchmarkEvent], list[BenchmarkEvent]]:
    pst_by_player: dict[str, list[BenchmarkEvent]] = defaultdict(list)
    nba_by_player: dict[str, list[BenchmarkEvent]] = defaultdict(list)
    for event in pst:
        pst_by_player[event.player_key].append(event)
    for event in nba:
        nba_by_player[event.player_key].append(event)

    matches: list[tuple[BenchmarkEvent, BenchmarkEvent]] = []
    used_pst: set[int] = set()
    used_nba: set[int] = set()
    for player_key in sorted(set(pst_by_player) | set(nba_by_player)):
        left = sorted(
            pst_by_player[player_key], key=lambda event: (event.event_date, event.source_id)
        )
        right = sorted(
            nba_by_player[player_key], key=lambda event: (event.event_date, event.source_id)
        )
        for left_index, right_index in _optimal_player_matches(left, right):
            left_event = left[left_index]
            right_event = right[right_index]
            matches.append((left_event, right_event))
            used_pst.add(left_event.source_id)
            used_nba.add(right_event.source_id)

    matches.sort(key=lambda pair: (pair[1].event_date, pair[1].source_id, pair[0].source_id))
    return (
        matches,
        sorted(
            (event for event in pst if event.source_id not in used_pst),
            key=lambda event: (event.event_date, event.source_id),
        ),
        sorted(
            (event for event in nba if event.source_id not in used_nba),
            key=lambda event: (event.event_date, event.source_id),
        ),
    )


def _candidate_count(event: BenchmarkEvent, candidates: list[BenchmarkEvent]) -> int:
    return sum(
        event.player_key == candidate.player_key
        and abs((event.event_date - candidate.event_date).days) <= MAX_MATCH_DAYS
        for candidate in candidates
    )


def _describe_matches(
    pairs: list[tuple[BenchmarkEvent, BenchmarkEvent]],
    pst: list[BenchmarkEvent],
    nba: list[BenchmarkEvent],
) -> list[EventMatch]:
    return [
        EventMatch(
            pst=left,
            nba=right,
            day_difference=(right.event_date - left.event_date).days,
            body_agreement=_body_agreement(left.body_part, right.body_part),
            injury_type_agreement=_agreement(left.injury_type, right.injury_type),
            laterality_agreement=_agreement(left.laterality, right.laterality),
            reason_similarity=_reason_similarity(left, right),
            pst_candidate_count=_candidate_count(left, nba),
            nba_candidate_count=_candidate_count(right, pst),
        )
        for left, right in pairs
    ]


def derive_overlap(
    session: Session,
    requested_start: date | None = None,
    requested_end: date | None = None,
) -> tuple[date, date]:
    pst_min, pst_max = session.execute(select(func.min(Injury.date), func.max(Injury.date))).one()
    nba_min, nba_max = session.execute(
        select(func.min(NBAInjuryEpisode.start_date), func.max(NBAInjuryEpisode.start_date))
    ).one()
    if not all((pst_min, pst_max, nba_min, nba_max)):
        raise ValueError("NBA episode and PST Injury tables must both contain data")
    start_date = max(pst_min, nba_min, CONTINUOUS_NBA_COVERAGE_START)
    end_date = min(pst_max, nba_max)
    if requested_start is not None:
        start_date = max(start_date, requested_start)
    if requested_end is not None:
        end_date = min(end_date, requested_end)
    if start_date > end_date:
        raise ValueError("requested dates do not overlap usable NBA and PST coverage")
    return start_date, end_date


def _load_events(
    session: Session,
    start_date: date,
    end_date: date,
    player_keys: set[str] | None = None,
) -> tuple[list[BenchmarkEvent], list[BenchmarkEvent]]:
    pst = [
        BenchmarkEvent(
            source_id=row.id,
            source_parent_id=row.source_raw_transaction_id,
            player_key=player_name_key(row.player_name),
            player_name=row.player_name,
            event_date=row.date,
            body_part=row.body_part,
            injury_type=row.injury_type,
            laterality=_extract_laterality(row.notes),
            reason=_normalize_reason(row.notes),
        )
        for row in session.scalars(
            select(Injury)
            .where(Injury.date >= start_date, Injury.date <= end_date)
            .order_by(Injury.date, Injury.id)
        )
        if player_keys is None or player_name_key(row.player_name) in player_keys
    ]

    nba_statement = (
        select(NBAInjuryEpisode, NBAPlayer)
        .join(NBAPlayer, NBAPlayer.id == NBAInjuryEpisode.player_id)
        .where(
            NBAInjuryEpisode.start_date >= start_date,
            NBAInjuryEpisode.start_date <= end_date,
        )
        .order_by(NBAInjuryEpisode.start_date, NBAInjuryEpisode.id)
    )
    if player_keys is not None:
        nba_statement = nba_statement.where(NBAPlayer.name_key.in_(sorted(player_keys)))
    episode_rows = list(session.execute(nba_statement))
    episode_ids = [episode.id for episode, _player in episode_rows]
    lineage_by_episode: dict[int, tuple[int, int, int, int]] = {}
    if episode_ids:
        lineage = (
            select(
                NBAInjuryEpisodeCondition.injury_episode_id.label("episode_id"),
                func.count().label("lineage_count"),
                func.min(NBAInjuryEpisodeCondition.injury_condition_id).label("first_condition_id"),
            )
            .where(NBAInjuryEpisodeCondition.injury_episode_id.in_(episode_ids))
            .group_by(NBAInjuryEpisodeCondition.injury_episode_id)
            .subquery()
        )
        lineage_by_episode = {
            episode_id: (lineage_count, condition_id, report_entry_id, report_id)
            for (
                episode_id,
                lineage_count,
                condition_id,
                report_entry_id,
                report_id,
            ) in session.execute(
                select(
                    lineage.c.episode_id,
                    lineage.c.lineage_count,
                    lineage.c.first_condition_id,
                    NBAInjuryCondition.report_entry_id,
                    NBAReportEntry.report_id,
                )
                .join(NBAInjuryCondition, NBAInjuryCondition.id == lineage.c.first_condition_id)
                .join(NBAReportEntry, NBAReportEntry.id == NBAInjuryCondition.report_entry_id)
            )
        }
    nba = [
        BenchmarkEvent(
            source_id=episode.id,
            player_key=player.name_key,
            player_name=player.canonical_name,
            event_date=episode.start_date,
            body_part=episode.body_part,
            injury_type=episode.injury_type,
            laterality=episode.laterality,
            reason=episode.normalized_reason,
            lineage_count=lineage_by_episode[episode.id][0],
            first_condition_id=lineage_by_episode[episode.id][1],
            first_report_entry_id=lineage_by_episode[episode.id][2],
            first_report_id=lineage_by_episode[episode.id][3],
        )
        for episode, player in episode_rows
    ]
    return pst, nba


def _event_row(event: BenchmarkEvent, source: str) -> dict[str, object]:
    row: dict[str, object] = {
        "source": source,
        "player_key": event.player_key,
        "player": event.player_name,
        "event_date": event.event_date.isoformat(),
        "body_part": event.body_part,
        "injury_type": event.injury_type,
        "laterality": event.laterality,
        "normalized_reason": event.reason,
    }
    if source == "nba":
        row.update(
            {
                "nba_episode_id": event.source_id,
                "nba_lineage_count": event.lineage_count,
                "nba_first_condition_id": event.first_condition_id,
                "nba_first_report_entry_id": event.first_report_entry_id,
                "nba_first_report_id": event.first_report_id,
            }
        )
    else:
        row.update(
            {
                "pst_injury_id": event.source_id,
                "pst_raw_transaction_id": event.source_parent_id,
            }
        )
    return row


def _discrepancy_categories(match: EventMatch) -> list[str]:
    categories: list[str] = []
    if match.day_difference:
        categories.append("date_timing_discrepancy")
    if match.body_agreement is False:
        categories.append("body_disagreement")
    if match.injury_type_agreement is False:
        categories.append("injury_type_disagreement")
    if match.laterality_agreement is False:
        categories.append("laterality_disagreement")
    if match.ambiguous:
        categories.append("ambiguous_multiple_candidate_match")
    if (
        match.body_agreement is None
        and match.injury_type_agreement is None
        and match.laterality_agreement is None
        and match.reason_similarity < 0.5
    ):
        categories.append("unresolved")
    return categories


def _match_row(match: EventMatch) -> dict[str, object]:
    categories = _discrepancy_categories(match)
    return {
        "category": "matched",
        "discrepancy_categories": ";".join(categories),
        "pst_injury_id": match.pst.source_id,
        "pst_raw_transaction_id": match.pst.source_parent_id,
        "pst_player": match.pst.player_name,
        "pst_date": match.pst.event_date.isoformat(),
        "pst_body_part": match.pst.body_part,
        "pst_injury_type": match.pst.injury_type,
        "pst_laterality": match.pst.laterality,
        "pst_normalized_reason": match.pst.reason,
        "nba_episode_id": match.nba.source_id,
        "nba_lineage_count": match.nba.lineage_count,
        "nba_first_condition_id": match.nba.first_condition_id,
        "nba_first_report_entry_id": match.nba.first_report_entry_id,
        "nba_first_report_id": match.nba.first_report_id,
        "nba_player": match.nba.player_name,
        "nba_date": match.nba.event_date.isoformat(),
        "nba_body_part": match.nba.body_part,
        "nba_injury_type": match.nba.injury_type,
        "nba_laterality": match.nba.laterality,
        "nba_normalized_reason": match.nba.reason,
        "date_difference_days": match.day_difference,
        "absolute_date_difference_days": abs(match.day_difference),
        "exact_date": match.day_difference == 0,
        "within_1_day": abs(match.day_difference) <= 1,
        "within_3_days": abs(match.day_difference) <= 3,
        "within_7_days": True,
        "body_comparable": match.body_agreement is not None,
        "body_agreement": match.body_agreement,
        "injury_type_comparable": match.injury_type_agreement is not None,
        "injury_type_agreement": match.injury_type_agreement,
        "laterality_comparable": match.laterality_agreement is not None,
        "laterality_agreement": match.laterality_agreement,
        "reason_similarity": match.reason_similarity,
        "ambiguous_multiple_candidate_match": match.ambiguous,
        "pst_candidate_count": match.pst_candidate_count,
        "nba_candidate_count": match.nba_candidate_count,
    }


def _percentage(numerator: int, denominator: int) -> float | None:
    return round(numerator * 100 / denominator, 3) if denominator else None


def _agreement_summary(matches: list[EventMatch], attribute: str) -> dict[str, object]:
    values = [getattr(match, attribute) for match in matches]
    comparable = [value for value in values if value is not None]
    agreed = sum(value is True for value in comparable)
    return {
        "comparable_matches": len(comparable),
        "agreements": agreed,
        "agreement_percentage": _percentage(agreed, len(comparable)),
    }


def build_benchmark_from_events(
    pst: list[BenchmarkEvent],
    nba: list[BenchmarkEvent],
    start_date: date,
    end_date: date,
    selected_player_keys: list[str] | None = None,
) -> BenchmarkResult:
    pairs, pst_unmatched, nba_unmatched = _match_events(pst, nba)
    described = _describe_matches(pairs, pst, nba)
    match_rows = [_match_row(match) for match in described]
    pst_only_rows = [_event_row(event, "pst") for event in pst_unmatched]
    nba_only_rows = [_event_row(event, "nba") for event in nba_unmatched]

    discrepancy_rows = (
        [row for row in match_rows if row["discrepancy_categories"]]
        + [
            {"category": "pst_only", "discrepancy_categories": "pst_only;unresolved", **row}
            for row in pst_only_rows
        ]
        + [
            {"category": "nba_only", "discrepancy_categories": "nba_only;unresolved", **row}
            for row in nba_only_rows
        ]
    )
    discrepancy_rows.sort(
        key=lambda row: (
            str(row.get("nba_date") or row.get("event_date") or row.get("pst_date")),
            str(row.get("category")),
            int(row.get("nba_episode_id") or row.get("pst_injury_id") or 0),
        )
    )

    category_counts: Counter[str] = Counter()
    for row in discrepancy_rows:
        category_counts.update(str(row["discrepancy_categories"]).split(";"))
    exact = sum(match.day_difference == 0 for match in described)
    within = {
        str(days): sum(abs(match.day_difference) <= days for match in described)
        for days in (1, 3, 7)
    }
    seasons: dict[str, dict[str, int]] = {}
    for season in sorted({get_official_nba_season(event.event_date) for event in pst + nba}):
        seasons[season] = {
            "pst_events": sum(get_official_nba_season(event.event_date) == season for event in pst),
            "nba_episodes": sum(
                get_official_nba_season(event.event_date) == season for event in nba
            ),
            "matches": sum(
                get_official_nba_season(match.nba.event_date) == season for match in described
            ),
        }

    digest_payload = {
        "matches": match_rows,
        "nba_only": nba_only_rows,
        "pst_only": pst_only_rows,
        "discrepancies": discrepancy_rows,
    }
    benchmark_digest = sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    summary: dict[str, object] = {
        "benchmark_version": BENCHMARK_VERSION,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "selected_player_keys": selected_player_keys,
        "pst_events": len(pst),
        "nba_episodes": len(nba),
        "pst_unique_players": len({event.player_key for event in pst}),
        "nba_unique_players": len({event.player_key for event in nba}),
        "matched": len(described),
        "nba_only": len(nba_only_rows),
        "pst_only": len(pst_only_rows),
        "outcome_category_counts": {
            "matched": len(described),
            "nba_only": len(nba_only_rows),
            "pst_only": len(pst_only_rows),
        },
        "exact_date_matches": exact,
        "within_days": within,
        "nba_match_rate_percentage": _percentage(len(described), len(nba)),
        "pst_match_rate_percentage": _percentage(len(described), len(pst)),
        "body_part": _agreement_summary(described, "body_agreement"),
        "injury_type": _agreement_summary(described, "injury_type_agreement"),
        "laterality": {
            **_agreement_summary(described, "laterality_agreement"),
            "pst_events_with_usable_laterality": sum(event.laterality is not None for event in pst),
            "note": "PST laterality is extracted from retained notes.",
        },
        "mean_reason_similarity": round(
            sum(match.reason_similarity for match in described) / len(described), 6
        )
        if described
        else None,
        "ambiguous_multiple_candidate_matches": sum(match.ambiguous for match in described),
        "discrepancy_category_counts": dict(sorted(category_counts.items())),
        "by_season": seasons,
        "benchmark_digest": benchmark_digest,
        "matching_note": (
            "PST is an external benchmark, not ground truth. Matching is same-player, "
            "one-to-one, order-preserving, and bounded to +/-7 days. The optimizer maximizes "
            "pair count, then minimizes date distance and explicit anatomy/type/laterality "
            "disagreements, then maximizes normalized reason similarity."
        ),
    }
    return BenchmarkResult(
        summary=summary,
        matches=match_rows,
        nba_only=nba_only_rows,
        pst_only=pst_only_rows,
        discrepancies=discrepancy_rows,
    )


def build_benchmark_result(
    session: Session,
    requested_start: date | None = None,
    requested_end: date | None = None,
    player_names: list[str] | None = None,
) -> BenchmarkResult:
    start_date, end_date = derive_overlap(session, requested_start, requested_end)
    player_keys = sorted({player_name_key(name) for name in player_names}) if player_names else None
    pst, nba = _load_events(
        session,
        start_date,
        end_date,
        set(player_keys) if player_keys else None,
    )
    return build_benchmark_from_events(pst, nba, start_date, end_date, player_keys)


def build_benchmark_report(session: Session, start_date: date, end_date: date) -> dict[str, object]:
    """Compatibility entry point returning the summary without writing artifacts."""

    return build_benchmark_result(session, start_date, end_date).summary


def _csv_text(rows: list[dict[str, object]]) -> str:
    if not rows:
        return ""
    fieldnames = sorted({key for row in rows for key in row})
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_benchmark_artifacts(
    result: BenchmarkResult, output_directory: Path = DEFAULT_OUTPUT_DIRECTORY
) -> dict[str, Path]:
    paths = {
        "summary": output_directory / "nba_vs_pst_summary.json",
        "matches": output_directory / "nba_vs_pst_matches.csv",
        "nba_only": output_directory / "nba_vs_pst_nba_only.csv",
        "pst_only": output_directory / "nba_vs_pst_pst_only.csv",
        "discrepancies": output_directory / "nba_vs_pst_discrepancies.csv",
    }
    _atomic_write(paths["matches"], _csv_text(result.matches))
    _atomic_write(paths["nba_only"], _csv_text(result.nba_only))
    _atomic_write(paths["pst_only"], _csv_text(result.pst_only))
    _atomic_write(paths["discrepancies"], _csv_text(result.discrepancies))
    # Summary is the completion marker and is replaced only after every CSV succeeds.
    _atomic_write(paths["summary"], json.dumps(result.summary, indent=2, sort_keys=True) + "\n")
    return paths
