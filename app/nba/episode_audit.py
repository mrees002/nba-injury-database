from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    NBAInjuryCondition,
    NBAInjuryEpisode,
    NBAInjuryEpisodeCondition,
    NBAReport,
    NBAReportEntry,
)
from app.nba.seasons import get_official_nba_season


def _percentile(values: list[int], fraction: float) -> float:
    if not values:
        return 0
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return round(values[lower] * (1 - weight) + values[upper] * weight, 3)


def build_episode_audit(session: Session) -> dict[str, object]:
    episodes = list(
        session.execute(
            select(
                NBAInjuryEpisode.id,
                NBAInjuryEpisode.player_id,
                NBAInjuryEpisode.team_id,
                NBAInjuryEpisode.start_date,
                NBAInjuryEpisode.last_observed_date,
                NBAInjuryEpisode.first_available_date,
                NBAInjuryEpisode.body_part,
                NBAInjuryEpisode.laterality,
                NBAInjuryEpisode.injury_type,
                NBAInjuryEpisode.normalized_reason,
                NBAInjuryEpisode.methodology_version,
            ).order_by(NBAInjuryEpisode.id)
        )
    )
    episode_ids = {episode.id for episode in episodes}
    observation_counts = Counter(
        {
            episode_id: count
            for episode_id, count in session.execute(
                select(
                    NBAInjuryEpisodeCondition.injury_episode_id,
                    func.count(),
                ).group_by(NBAInjuryEpisodeCondition.injury_episode_id)
            ).tuples()
        }
    )
    condition_type_counts: dict[int, set[str | None]] = defaultdict(set)
    status_sequences: dict[int, list[str | None]] = defaultdict(list)
    linked_team_ids: dict[int, set[int | None]] = defaultdict(set)
    lineage_rows = session.execute(
        select(
            NBAInjuryEpisodeCondition.injury_episode_id,
            NBAInjuryCondition.injury_type,
            NBAReportEntry.team_id,
            NBAReportEntry.status,
        )
        .join(
            NBAInjuryCondition,
            NBAInjuryCondition.id == NBAInjuryEpisodeCondition.injury_condition_id,
        )
        .join(NBAReportEntry, NBAReportEntry.id == NBAInjuryCondition.report_entry_id)
        .join(NBAReport, NBAReport.id == NBAReportEntry.report_id)
        .order_by(
            NBAInjuryEpisodeCondition.injury_episode_id,
            NBAReport.report_date,
            NBAReport.report_time,
            NBAReportEntry.id,
            NBAInjuryCondition.condition_index,
        )
        .execution_options(yield_per=5_000)
    )
    for episode_id, injury_type, team_id, status in lineage_rows:
        condition_type_counts[episode_id].add(injury_type)
        linked_team_ids[episode_id].add(team_id)
        statuses = status_sequences[episode_id]
        if not statuses or statuses[-1] != status:
            statuses.append(status)

    observations = [observation_counts[episode.id] for episode in episodes]
    durations = sorted(
        (episode.last_observed_date - episode.start_date).days for episode in episodes
    )
    season_counts = Counter(get_official_nba_season(episode.start_date) for episode in episodes)
    laterality_counts = Counter(episode.laterality or "<null>" for episode in episodes)
    methodology_counts = Counter(episode.methodology_version for episode in episodes)

    recurrences: Counter[tuple[int, int | None, str, str | None]] = Counter()
    for episode in episodes:
        if episode.body_part:
            recurrences[
                (episode.player_id, episode.team_id, episode.body_part, episode.laterality)
            ] += 1

    ordered_by_identity: dict[tuple[int, int | None, str | None, str | None], list[object]] = (
        defaultdict(list)
    )
    for episode in episodes:
        ordered_by_identity[
            (episode.player_id, episode.team_id, episode.body_part, episode.laterality)
        ].append(episode)
    fragmented_pairs = 0
    for identity_episodes in ordered_by_identity.values():
        identity_episodes.sort(key=lambda item: (item.start_date, item.id))
        fragmented_pairs += sum(
            0 <= (later.start_date - earlier.last_observed_date).days <= 3
            for earlier, later in zip(identity_episodes, identity_episodes[1:], strict=False)
        )

    linked_conditions = (
        session.scalar(select(func.count()).select_from(NBAInjuryEpisodeCondition)) or 0
    )
    injury_conditions = (
        session.scalar(
            select(func.count())
            .select_from(NBAInjuryCondition)
            .where(NBAInjuryCondition.is_injury.is_(True))
        )
        or 0
    )
    unique_linked_conditions = (
        session.scalar(
            select(func.count(func.distinct(NBAInjuryEpisodeCondition.injury_condition_id)))
        )
        or 0
    )

    return {
        "episodes": len(episodes),
        "unique_players": len({episode.player_id for episode in episodes}),
        "episodes_by_season": dict(sorted(season_counts.items())),
        "methodology_versions": dict(sorted(methodology_counts.items())),
        "linked_injury_conditions": linked_conditions,
        "unique_linked_injury_conditions": unique_linked_conditions,
        "unlinked_injury_conditions": injury_conditions - unique_linked_conditions,
        "episodes_without_lineage": len(episode_ids - observation_counts.keys()),
        "episodes_with_cross_team_lineage": sum(
            len({team_id for team_id in team_ids if team_id is not None}) > 1
            for team_ids in linked_team_ids.values()
        ),
        "median_observations_per_episode": median(observations) if observations else 0,
        "single_observation_episodes": sum(count == 1 for count in observations),
        "single_observation_episode_percentage": round(
            sum(count == 1 for count in observations) * 100 / len(observations), 3
        )
        if observations
        else 0,
        "maximum_observations_per_episode": max(observations, default=0),
        "duration_days": {
            "median": median(durations) if durations else 0,
            "p75": _percentile(durations, 0.75),
            "p90": _percentile(durations, 0.90),
            "p99": _percentile(durations, 0.99),
            "maximum": max(durations, default=0),
            "same_day": sum(days == 0 for days in durations),
            "one_to_three": sum(1 <= days <= 3 for days in durations),
            "four_to_seven": sum(4 <= days <= 7 for days in durations),
            "eight_to_fourteen": sum(8 <= days <= 14 for days in durations),
            "fifteen_to_thirty": sum(15 <= days <= 30 for days in durations),
            "thirty_one_to_ninety": sum(31 <= days <= 90 for days in durations),
            "over_ninety": sum(days > 90 for days in durations),
        },
        "episodes_with_status_changes": sum(
            len(statuses) > 1 for statuses in status_sequences.values()
        ),
        "episodes_with_multiple_status_changes": sum(
            len(statuses) > 2 for statuses in status_sequences.values()
        ),
        "episodes_with_multiple_linked_conditions": sum(count > 1 for count in observations),
        "episodes_with_multiple_condition_types": sum(
            len(types) > 1 for types in condition_type_counts.values()
        ),
        "episodes_closed_by_available": sum(
            episode.first_available_date is not None for episode in episodes
        ),
        "recurrence_identity_groups": sum(count > 1 for count in recurrences.values()),
        "recurrence_episodes_after_first": sum(max(count - 1, 0) for count in recurrences.values()),
        "same_identity_episode_pairs_within_three_days": fragmented_pairs,
        "laterality": dict(sorted(laterality_counts.items())),
    }
