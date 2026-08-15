from __future__ import annotations

from datetime import date, time

import pytest

from app.nba.episodes import METHODOLOGY_VERSION, EpisodeObservation, build_episode_candidates


def test_episode_methodology_has_an_explicit_version():
    assert METHODOLOGY_VERSION == "nba-episodes-v3"


def _observation(
    condition_id: int,
    day: int,
    *,
    laterality: str | None = "right",
    status: str = "Out",
    reason: str = "injury illness right ankle sprain",
    body_part: str | None = "ankle",
    injury_type: str | None = "sprain",
    report_entry_id: int | None = None,
    report_id: int | None = None,
    report_day: int | None = None,
    report_time: time = time(12),
    team_id: int = 20,
):
    return EpisodeObservation(
        condition_id=condition_id,
        report_entry_id=report_entry_id or condition_id,
        report_id=report_id or condition_id,
        player_id=10,
        team_id=team_id,
        game_date=date(2026, 1, day),
        report_date=date(2026, 1, report_day or day),
        report_time=report_time,
        status=status,
        body_part=body_part,
        laterality=laterality,
        injury_type=injury_type,
        normalized_reason=reason,
    )


def test_repeated_status_snapshots_become_one_episode():
    episodes = build_episode_candidates(
        [_observation(1, 1, status="Questionable"), _observation(2, 2), _observation(3, 4)]
    )
    assert len(episodes) == 1
    assert episodes[0].condition_ids == [1, 2, 3]
    assert episodes[0].start_date == date(2026, 1, 1)
    assert episodes[0].last_observed_date == date(2026, 1, 4)


def test_laterality_is_not_merged_and_available_closes_episode():
    episodes = build_episode_candidates(
        [
            _observation(1, 1, laterality="left", reason="left ankle sprain"),
            _observation(2, 2, laterality="right", reason="right ankle sprain"),
            _observation(3, 3, laterality="left", reason="left ankle sprain", status="Available"),
            _observation(4, 5, laterality="left", reason="left ankle sprain"),
        ]
    )
    assert len(episodes) == 3
    left = [episode for episode in episodes if episode.condition_ids == [1, 3]][0]
    assert left.first_available_date == date(2026, 1, 3)


def test_unclassified_body_part_is_not_dropped():
    episodes = build_episode_candidates(
        [_observation(1, 1, laterality=None, body_part=None, injury_type=None, reason="rare issue")]
    )
    assert len(episodes) == 1
    assert episodes[0].normalized_reason == "rare issue"


def test_recurrence_after_exact_reason_gap_starts_new_episode():
    episodes = build_episode_candidates(
        [_observation(1, 1), _observation(2, 16), _observation(3, 17)]
    )
    assert [episode.condition_ids for episode in episodes] == [[1], [2, 3]]


def test_short_disappearance_does_not_imply_recovery():
    episodes = build_episode_candidates([_observation(1, 1), _observation(2, 11)])
    assert [episode.condition_ids for episode in episodes] == [[1, 2]]


def test_long_disappearance_starts_a_new_episode_even_for_exact_reason():
    episodes = build_episode_candidates([_observation(1, 1), _observation(2, 20)])
    assert [episode.condition_ids for episode in episodes] == [[1], [2]]


def test_later_same_day_status_supersedes_available_transition():
    episodes = build_episode_candidates(
        [
            _observation(1, 1, status="Out"),
            _observation(2, 2, status="Available"),
            _observation(3, 2, status="Questionable"),
            _observation(4, 3, status="Out"),
        ]
    )
    assert len(episodes) == 1
    assert episodes[0].first_available_date is None
    assert episodes[0].condition_ids == [1, 2, 3, 4]


def test_same_body_surgery_and_recovery_sequence_stays_one_episode():
    episodes = build_episode_candidates(
        [
            _observation(1, 1, injury_type="surgery", reason="right knee surgery"),
            _observation(2, 5, injury_type="recovery", reason="right knee recovery"),
        ]
    )
    assert len(episodes) == 1
    assert episodes[0].condition_ids == [1, 2]


def test_fracture_surgery_and_recovery_sequence_stays_one_episode():
    episodes = build_episode_candidates(
        [
            _observation(1, 1, injury_type="fracture", reason="right foot fracture"),
            _observation(2, 5, injury_type="surgery", reason="right foot surgery"),
            _observation(3, 9, injury_type="recovery", reason="right foot surgery recovery"),
        ]
    )
    assert [episode.condition_ids for episode in episodes] == [[1, 2, 3]]


def test_generic_tendon_wording_continues_a_stated_tear():
    episodes = build_episode_candidates(
        [
            _observation(
                1,
                1,
                body_part="achilles",
                injury_type="tear",
                reason="right achilles tendon tear",
            ),
            _observation(
                2,
                3,
                body_part="achilles",
                injury_type="tendon injury",
                reason="right achilles tendon",
            ),
        ]
    )
    assert [episode.condition_ids for episode in episodes] == [[1, 2]]
    assert episodes[0].injury_type == "tear"


def test_ligament_injury_to_stated_tear_continues_one_episode():
    episodes = build_episode_candidates(
        [
            _observation(
                1,
                1,
                body_part="knee",
                injury_type="ACL injury",
                reason="right acl injury",
            ),
            _observation(
                2,
                4,
                body_part="knee",
                injury_type="ACL tear",
                reason="right acl tear",
            ),
        ]
    )
    assert [episode.condition_ids for episode in episodes] == [[1, 2]]


def test_clearly_different_same_body_injury_starts_a_new_episode():
    episodes = build_episode_candidates(
        [
            _observation(1, 1, body_part="knee", injury_type="ACL tear", reason="right acl tear"),
            _observation(
                2,
                2,
                body_part="knee",
                injury_type="soreness",
                reason="right knee soreness",
            ),
        ]
    )
    assert [episode.condition_ids for episode in episodes] == [[1], [2]]


def test_distinct_named_structures_on_same_body_do_not_merge():
    episodes = build_episode_candidates(
        [
            _observation(1, 1, body_part="knee", injury_type="tear", reason="right acl tear"),
            _observation(
                2,
                2,
                body_part="knee",
                injury_type="tear",
                reason="right meniscus tear",
            ),
        ]
    )
    assert [episode.condition_ids for episode in episodes] == [[1], [2]]


def test_chronic_soreness_and_compatible_wording_changes_continue():
    episodes = build_episode_candidates(
        [
            _observation(1, 1, injury_type="soreness", reason="right knee soreness"),
            _observation(2, 4, injury_type="pain", reason="right knee pain"),
            _observation(3, 8, injury_type="soreness", reason="right patellar soreness"),
        ]
    )
    assert [episode.condition_ids for episode in episodes] == [[1, 2, 3]]


def test_later_explicit_type_and_laterality_fill_unknown_episode_summary():
    episodes = build_episode_candidates(
        [
            _observation(
                1,
                1,
                laterality=None,
                injury_type=None,
                reason="knee condition",
                body_part="knee",
            ),
            _observation(
                2,
                2,
                laterality="right",
                injury_type="strain",
                reason="right knee strain",
                body_part="knee",
            ),
        ]
    )
    assert len(episodes) == 1
    assert (episodes[0].laterality, episodes[0].injury_type) == ("right", "strain")


@pytest.mark.parametrize(
    ("body_part", "laterality", "injury_type", "reason"),
    [
        ("head", None, "concussion", "concussion protocol"),
        ("achilles", "left", "tear", "left achilles tear"),
        ("hamstring", "right", "strain", "right hamstring strain"),
        ("groin", "left", "strain", "left groin strain"),
    ],
)
def test_repeated_nba_condition_examples_continue(body_part, laterality, injury_type, reason):
    episodes = build_episode_candidates(
        [
            _observation(
                1,
                1,
                body_part=body_part,
                laterality=laterality,
                injury_type=injury_type,
                reason=reason,
            ),
            _observation(
                2,
                3,
                body_part=body_part,
                laterality=laterality,
                injury_type=injury_type,
                reason=reason,
            ),
        ]
    )
    assert [episode.condition_ids for episode in episodes] == [[1, 2]]


def test_bilateral_and_unilateral_conditions_do_not_merge():
    episodes = build_episode_candidates(
        [
            _observation(1, 1, laterality="bilateral", reason="bilateral knee soreness"),
            _observation(2, 2, laterality="left", reason="left knee soreness"),
        ]
    )
    assert [episode.condition_ids for episode in episodes] == [[1], [2]]


def test_team_change_starts_a_new_episode():
    episodes = build_episode_candidates(
        [_observation(1, 1, team_id=20), _observation(2, 2, team_id=21)]
    )
    assert [episode.condition_ids for episode in episodes] == [[1], [2]]


def test_simultaneous_conditions_from_same_source_row_remain_separate():
    acl = _observation(1, 1, injury_type="ACL injury", reason="right knee acl")
    mcl = _observation(
        2,
        1,
        injury_type="MCL injury",
        reason="right knee mcl",
        report_entry_id=acl.report_entry_id,
    )

    episodes = build_episode_candidates([acl, mcl])

    assert [episode.condition_ids for episode in episodes] == [[1], [2]]


def test_separate_conditions_in_one_report_do_not_merge_across_source_rows():
    first = _observation(1, 1, report_id=100, report_entry_id=101)
    second = _observation(
        2,
        1,
        report_id=100,
        report_entry_id=102,
        body_part="knee",
        injury_type="soreness",
        reason="right knee soreness",
    )

    episodes = build_episode_candidates([first, second])

    assert [episode.condition_ids for episode in episodes] == [[1], [2]]


def test_report_publication_chronology_drives_gap_matching():
    episodes = build_episode_candidates(
        [
            _observation(1, 2, report_day=1),
            _observation(2, 16, report_day=15),
        ]
    )
    assert [episode.condition_ids for episode in episodes] == [[1, 2]]
