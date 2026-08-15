from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, time
from hashlib import sha256

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.models import (
    NBAInjuryCondition,
    NBAInjuryEpisode,
    NBAInjuryEpisodeCondition,
    NBAReport,
    NBAReportEntry,
)

METHODOLOGY_VERSION = "nba-episodes-v3"
AVAILABLE_STATUSES = {"available"}
EXACT_REASON_MAX_GAP_DAYS = 14
SAME_TYPE_MAX_GAP_DAYS = 7
COMPATIBLE_TYPE_MAX_GAP_DAYS = 7
WEAK_TYPE_MAX_GAP_DAYS = 3

_GENERIC_TYPES = {None, "injury", "injury management"}
_RECOVERY_TYPES = {"recovery", "surgery"}
_SYMPTOM_TYPES = {"inflammation", "pain", "soreness", "stiffness", "swelling", "tightness"}
_SPRAIN_STRAIN_TYPES = {"sprain", "strain"}
_CONTUSION_TYPES = {"bruise", "contusion"}
_TENDON_TYPES = {"inflammation", "tendinopathy", "tendon injury"}
_HEAD_TYPES = {"concussion", "headache"}
_LIGAMENT_REFINEMENTS = {
    ("ACL injury", "ACL tear"),
    ("MCL injury", "MCL tear"),
    ("PCL injury", "PCL tear"),
}
_SURGERY_PRECURSOR_TYPES = {
    "ACL injury",
    "ACL tear",
    "MCL injury",
    "MCL tear",
    "PCL injury",
    "PCL tear",
    "dislocation",
    "fracture",
    "impingement",
    "instability",
    "sprain",
    "strain",
    "subluxation",
    "tear",
    "tendinopathy",
    "tendon injury",
}
_NAMED_STRUCTURES = (
    (("acl", "anterior cruciate ligament"), "ACL"),
    (("mcl", "medial collateral ligament"), "MCL"),
    (("pcl", "posterior cruciate ligament"), "PCL"),
    (("meniscus", "meniscal"), "meniscus"),
    (("patella", "patellar"), "patella"),
    (("rotator cuff",), "rotator cuff"),
    (("labrum", "labral"), "labrum"),
    (("ac joint", "acromioclavicular"), "AC joint"),
    (("ucl",), "UCL"),
    (("plantar fascia",), "plantar fascia"),
    (("scaphoid",), "scaphoid"),
    (("tibia", "tibial"), "tibia"),
    (("fibula", "fibular"), "fibula"),
)


def _named_structure(normalized_reason: str) -> str | None:
    for terms, label in _NAMED_STRUCTURES:
        if any(
            re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", normalized_reason)
            for term in terms
        ):
            return label
    return None


def _is_more_specific_type(current: str | None, new: str | None) -> bool:
    if new is None:
        return False
    if current is None:
        return True
    if current == "tendon injury" and new in {"tear", "tendinopathy"}:
        return True
    return (current, new) in _LIGAMENT_REFINEMENTS


@dataclass
class EpisodeAccumulator:
    player_id: int
    team_id: int | None
    start_date: date
    last_observed_date: date
    body_part: str | None
    laterality: str | None
    injury_type: str | None
    normalized_reason: str
    latest_status: str | None
    last_report_date: date
    last_report_time: time
    named_structure: str | None
    condition_ids: list[int] = field(default_factory=list)
    report_entry_ids: set[int] = field(default_factory=set)
    first_available_date: date | None = None

    def add(
        self,
        observation: EpisodeObservation,
    ) -> None:
        self.last_observed_date = max(self.last_observed_date, observation.game_date)
        self.last_report_date = observation.report_date
        self.last_report_time = observation.report_time
        self.latest_status = observation.status
        if self.body_part is None and observation.body_part is not None:
            self.body_part = observation.body_part
        if self.laterality is None and observation.laterality is not None:
            self.laterality = observation.laterality
        if _is_more_specific_type(self.injury_type, observation.injury_type):
            self.injury_type = observation.injury_type
        self.condition_ids.append(observation.condition_id)
        self.report_entry_ids.add(observation.report_entry_id)
        if self.named_structure is None:
            self.named_structure = _named_structure(observation.normalized_reason)
        if (observation.status or "").lower() in AVAILABLE_STATUSES:
            if self.first_available_date is None:
                self.first_available_date = observation.game_date
        elif self.first_available_date == observation.game_date:
            # A later report version on the same date supersedes an earlier Available.
            self.first_available_date = None


@dataclass(frozen=True)
class EpisodeRebuildResult:
    observations: int
    episodes: int
    players: int


@dataclass(frozen=True)
class EpisodeObservation:
    condition_id: int
    report_entry_id: int
    report_id: int
    player_id: int
    team_id: int | None
    game_date: date
    report_date: date
    report_time: time
    status: str | None
    body_part: str | None
    laterality: str | None
    injury_type: str | None
    normalized_reason: str


def _compatible_type_score(
    episode_type: str | None,
    observation_type: str | None,
) -> tuple[int, int] | None:
    """Return a score and maximum gap for explicitly compatible type evolution."""

    if episode_type == observation_type:
        return 80, SAME_TYPE_MAX_GAP_DAYS
    if episode_type in _GENERIC_TYPES or observation_type in _GENERIC_TYPES:
        return 35, WEAK_TYPE_MAX_GAP_DAYS
    pair = {episode_type, observation_type}
    if pair <= _SYMPTOM_TYPES:
        return 60, COMPATIBLE_TYPE_MAX_GAP_DAYS
    if pair <= (_SYMPTOM_TYPES | _SPRAIN_STRAIN_TYPES) and pair & _SPRAIN_STRAIN_TYPES:
        return 55, COMPATIBLE_TYPE_MAX_GAP_DAYS
    if pair <= _CONTUSION_TYPES:
        return 60, COMPATIBLE_TYPE_MAX_GAP_DAYS
    if pair <= _TENDON_TYPES:
        return 55, COMPATIBLE_TYPE_MAX_GAP_DAYS
    if pair == {"tear", "tendon injury"}:
        return 55, COMPATIBLE_TYPE_MAX_GAP_DAYS
    if (episode_type, observation_type) in _LIGAMENT_REFINEMENTS or (
        observation_type,
        episode_type,
    ) in _LIGAMENT_REFINEMENTS:
        return 65, COMPATIBLE_TYPE_MAX_GAP_DAYS
    if pair <= _HEAD_TYPES:
        return 55, COMPATIBLE_TYPE_MAX_GAP_DAYS
    if pair <= _RECOVERY_TYPES:
        return 70, COMPATIBLE_TYPE_MAX_GAP_DAYS
    if "recovery" in pair:
        return 50, COMPATIBLE_TYPE_MAX_GAP_DAYS
    if "surgery" in pair and pair & _SURGERY_PRECURSOR_TYPES:
        return 50, COMPATIBLE_TYPE_MAX_GAP_DAYS
    return None


def _match_score(
    episode: EpisodeAccumulator,
    observation: EpisodeObservation,
) -> int | None:
    gap = (observation.report_date - episode.last_report_date).days
    if gap < 0:
        return None
    if observation.report_entry_id in episode.report_entry_ids:
        # Independently classified conditions in one source row are simultaneous, not status
        # snapshots of the same episode.
        return None
    if (
        episode.first_available_date is not None
        and observation.game_date > episode.first_available_date
    ):
        return None
    if (
        episode.laterality
        and observation.laterality
        and episode.laterality != observation.laterality
    ):
        return None
    if episode.team_id and observation.team_id and episode.team_id != observation.team_id:
        return None
    if episode.body_part and observation.body_part and episode.body_part != observation.body_part:
        return None
    observation_structure = _named_structure(observation.normalized_reason)
    if (
        episode.named_structure
        and observation_structure
        and episode.named_structure != observation_structure
    ):
        return None

    exact_reason = episode.normalized_reason == observation.normalized_reason
    same_body = bool(episode.body_part and episode.body_part == observation.body_part)
    if exact_reason and gap <= EXACT_REASON_MAX_GAP_DAYS:
        return 200 - gap
    compatibility = _compatible_type_score(episode.injury_type, observation.injury_type)
    if same_body and compatibility:
        type_score, max_gap = compatibility
        if gap <= max_gap:
            side_score = 20 if episode.laterality == observation.laterality else 0
            return 100 + type_score + side_score - gap
    if (
        compatibility
        and not episode.body_part
        and not observation.body_part
        and episode.injury_type == observation.injury_type
        and gap <= WEAK_TYPE_MAX_GAP_DAYS
    ):
        return 40 - gap
    return None


def build_episode_candidates(
    observations: Iterable[EpisodeObservation],
) -> list[EpisodeAccumulator]:
    by_player: dict[int, list[EpisodeAccumulator]] = {}
    for observation in observations:
        player_episodes = by_player.setdefault(observation.player_id, [])
        candidates = [
            (score, index, episode)
            for index, episode in enumerate(player_episodes)
            if (score := _match_score(episode, observation)) is not None
        ]
        if candidates:
            _, _, episode = max(candidates, key=lambda item: (item[0], -item[1]))
            episode.add(observation)
            continue
        episode = EpisodeAccumulator(
            player_id=observation.player_id,
            team_id=observation.team_id,
            start_date=observation.game_date,
            last_observed_date=observation.game_date,
            body_part=observation.body_part,
            laterality=observation.laterality,
            injury_type=observation.injury_type,
            normalized_reason=observation.normalized_reason,
            latest_status=observation.status,
            last_report_date=observation.report_date,
            last_report_time=observation.report_time,
            named_structure=_named_structure(observation.normalized_reason),
        )
        episode.add(observation)
        player_episodes.append(episode)
    return [episode for episodes in by_player.values() for episode in episodes]


def rebuild_injury_episodes(session: Session) -> EpisodeRebuildResult:
    observation_count = (
        session.scalar(
            select(func.count())
            .select_from(NBAInjuryCondition)
            .join(
                NBAReportEntry,
                NBAReportEntry.id == NBAInjuryCondition.report_entry_id,
            )
            .where(NBAInjuryCondition.is_injury.is_(True))
        )
        or 0
    )
    rows = session.execute(
        select(
            NBAInjuryCondition.id,
            NBAInjuryCondition.report_entry_id,
            NBAReportEntry.report_id,
            NBAReportEntry.player_id,
            NBAReportEntry.team_id,
            NBAReportEntry.game_date,
            NBAReport.report_date,
            NBAReport.report_time,
            NBAReportEntry.status,
            NBAInjuryCondition.body_part,
            NBAInjuryCondition.laterality,
            NBAInjuryCondition.injury_type,
            NBAInjuryCondition.normalized_reason,
        )
        .join(
            NBAReportEntry,
            NBAReportEntry.id == NBAInjuryCondition.report_entry_id,
        )
        .join(NBAReport, NBAReport.id == NBAReportEntry.report_id)
        .where(
            NBAInjuryCondition.is_injury.is_(True),
            NBAReportEntry.player_id.is_not(None),
        )
        .order_by(
            NBAReportEntry.player_id,
            NBAReport.report_date,
            NBAReport.report_time,
            NBAReportEntry.game_date,
            NBAReportEntry.id,
            NBAInjuryCondition.condition_index,
            NBAInjuryCondition.id,
        )
        .execution_options(yield_per=5_000)
    )
    candidates = build_episode_candidates(
        EpisodeObservation(
            condition_id=row.id,
            report_entry_id=row.report_entry_id,
            report_id=row.report_id,
            player_id=row.player_id,
            team_id=row.team_id,
            game_date=row.game_date,
            report_date=row.report_date,
            report_time=row.report_time,
            status=row.status,
            body_part=row.body_part,
            laterality=row.laterality,
            injury_type=row.injury_type,
            normalized_reason=row.normalized_reason,
        )
        for row in rows
    )

    session.execute(delete(NBAInjuryEpisodeCondition))
    session.execute(delete(NBAInjuryEpisode))
    session.flush()
    episode_rows: list[tuple[EpisodeAccumulator, NBAInjuryEpisode]] = []
    for candidate in candidates:
        episode_rows.append(
            (
                candidate,
                NBAInjuryEpisode(
                    player_id=candidate.player_id,
                    team_id=candidate.team_id,
                    start_date=candidate.start_date,
                    last_observed_date=candidate.last_observed_date,
                    end_date=candidate.first_available_date,
                    first_available_date=candidate.first_available_date,
                    body_part=candidate.body_part,
                    laterality=candidate.laterality,
                    injury_type=candidate.injury_type,
                    normalized_reason=candidate.normalized_reason,
                    latest_status=candidate.latest_status,
                    methodology_version=METHODOLOGY_VERSION,
                ),
            )
        )
    session.add_all([episode for _, episode in episode_rows])
    session.flush()
    lineage_batch: list[dict[str, int]] = []
    for candidate, episode in episode_rows:
        lineage_batch.extend(
            {
                "injury_episode_id": episode.id,
                "injury_condition_id": condition_id,
            }
            for condition_id in candidate.condition_ids
        )
        if len(lineage_batch) >= 10_000:
            session.execute(insert(NBAInjuryEpisodeCondition), lineage_batch)
            lineage_batch.clear()
    if lineage_batch:
        session.execute(insert(NBAInjuryEpisodeCondition), lineage_batch)
    session.commit()
    return EpisodeRebuildResult(
        observations=observation_count,
        episodes=len(candidates),
        players=len({candidate.player_id for candidate in candidates}),
    )


def episode_semantic_digest(session: Session) -> str:
    """Hash derived episode content and lineage while ignoring generated episode IDs."""

    digest = sha256()
    rows = session.execute(
        select(
            NBAInjuryEpisode.id,
            NBAInjuryEpisode.player_id,
            NBAInjuryEpisode.team_id,
            NBAInjuryEpisode.start_date,
            NBAInjuryEpisode.last_observed_date,
            NBAInjuryEpisode.end_date,
            NBAInjuryEpisode.first_available_date,
            NBAInjuryEpisode.body_part,
            NBAInjuryEpisode.laterality,
            NBAInjuryEpisode.injury_type,
            NBAInjuryEpisode.normalized_reason,
            NBAInjuryEpisode.latest_status,
            NBAInjuryEpisode.methodology_version,
            NBAInjuryEpisodeCondition.injury_condition_id,
        )
        .join(
            NBAInjuryEpisodeCondition,
            NBAInjuryEpisodeCondition.injury_episode_id == NBAInjuryEpisode.id,
        )
        .order_by(NBAInjuryEpisode.id, NBAInjuryEpisodeCondition.injury_condition_id)
        .execution_options(yield_per=5_000)
    )
    for row in rows:
        semantic_values = row[1:]
        digest.update(
            "\x1f".join(
                "<null>" if value is None else str(value) for value in semantic_values
            ).encode()
        )
        digest.update(b"\n")
    return digest.hexdigest()
