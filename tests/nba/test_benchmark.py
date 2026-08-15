import csv
import json
from dataclasses import replace
from datetime import date

from app.nba.benchmark import (
    BENCHMARK_VERSION,
    BenchmarkEvent,
    _body_family,
    _extract_laterality,
    _match_events,
    build_benchmark_from_events,
    write_benchmark_artifacts,
)


def _event(source_id: int, day: int, *, source: str) -> BenchmarkEvent:
    return BenchmarkEvent(
        source_id=source_id,
        player_key="playerone",
        player_name="Player One",
        event_date=date(2026, 1, day),
        body_part="ankle",
        injury_type="sprain",
        laterality="right",
        reason=f"{source} right ankle sprain",
    )


def test_benchmark_matching_is_one_to_one_and_date_bounded():
    pst = [_event(1, 1, source="pst"), _event(2, 20, source="pst")]
    nba = [
        _event(10, 2, source="nba"),
        _event(11, 3, source="nba"),
        _event(12, 30, source="nba"),
    ]

    matches, pst_only, nba_only = _match_events(pst, nba)

    assert [(left.source_id, right.source_id) for left, right in matches] == [(1, 10)]
    assert [event.source_id for event in pst_only] == [2]
    assert [event.source_id for event in nba_only] == [11, 12]


def test_matching_maximizes_pair_count_before_date_distance():
    pst = [_event(1, 1, source="pst"), _event(2, 10, source="pst")]
    nba = [_event(10, 7, source="nba"), _event(11, 16, source="nba")]

    matches, pst_only, nba_only = _match_events(pst, nba)

    assert [(left.source_id, right.source_id) for left, right in matches] == [
        (1, 10),
        (2, 11),
    ]
    assert pst_only == []
    assert nba_only == []


def test_pst_laterality_is_extracted_from_retained_notes():
    assert _extract_laterality("placed on IL with a left ankle sprain") == "left"
    assert _extract_laterality("right and left knee soreness") == "bilateral"
    assert _extract_laterality("illness") is None


def test_body_agreement_normalizes_only_direct_anatomical_synonyms():
    assert _body_family("lower leg") == _body_family("shin") == "leg"
    assert _body_family("forearm") == _body_family("arm") == "arm"
    assert _body_family("foot") != _body_family("ankle")


def test_benchmark_artifacts_are_deterministic_and_preserve_trace_ids(tmp_path):
    pst = [
        replace(_event(1, 1, source="pst"), source_parent_id=101),
        replace(_event(2, 20, source="pst"), source_parent_id=102),
    ]
    nba = [
        replace(
            _event(10, 2, source="nba"),
            lineage_count=4,
            first_condition_id=1001,
            first_report_entry_id=2001,
            first_report_id=3001,
        ),
        replace(
            _event(11, 3, source="nba"),
            lineage_count=2,
            first_condition_id=1002,
            first_report_entry_id=2002,
            first_report_id=3002,
        ),
    ]

    first = build_benchmark_from_events(pst, nba, date(2026, 1, 1), date(2026, 1, 31))
    paths = write_benchmark_artifacts(first, tmp_path)
    first_bytes = {name: path.read_bytes() for name, path in paths.items()}
    second = build_benchmark_from_events(pst, nba, date(2026, 1, 1), date(2026, 1, 31))
    write_benchmark_artifacts(second, tmp_path)

    assert first.summary["benchmark_version"] == BENCHMARK_VERSION
    assert first.summary["benchmark_digest"] == second.summary["benchmark_digest"]
    assert first_bytes == {name: path.read_bytes() for name, path in paths.items()}
    assert json.loads(paths["summary"].read_text())["matched"] == 1
    with paths["matches"].open(newline="") as stream:
        match = next(csv.DictReader(stream))
    assert (match["pst_injury_id"], match["pst_raw_transaction_id"]) == ("1", "101")
    assert (
        match["nba_episode_id"],
        match["nba_first_condition_id"],
        match["nba_first_report_entry_id"],
        match["nba_first_report_id"],
    ) == ("10", "1001", "2001", "3001")
    assert match["ambiguous_multiple_candidate_match"] == "True"
    assert "ambiguous_multiple_candidate_match" in match["discrepancy_categories"]
    assert len(first.nba_only) == 1
    assert len(first.pst_only) == 1
