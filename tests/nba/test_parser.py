from __future__ import annotations

from datetime import datetime

import pytest

from app.nba.parser import (
    NBAReportParseError,
    Report,
    extract_date_matchups,
    parse_report_pdf,
    select_latest_reports,
    select_latest_reports_from_pairs,
)
from tests.nba.conftest import build_text_pdf


def test_parses_each_observed_format(nba_pdf_fixture: tuple[str, bytes]):
    fixture_name, content = nba_pdf_fixture
    parsed = parse_report_pdf(content, source_url=f"fixture:{fixture_name}")

    expected_format = {
        "legacy_category_v1": "legacy-category-v1",
        "legacy_reason_first_v1c": "legacy-reason-first-v1c",
        "legacy_current_previous_v1d": "legacy-current-previous-v1d",
        "legacy_status_history_v1b": "legacy-status-history-v1b",
        "standard_v2": "standard-v2",
        "compact_v3": "compact-v3",
    }[fixture_name]
    assert parsed.format_version == expected_format
    assert parsed.entries
    assert all(entry.game_date and entry.matchup and entry.team for entry in parsed.entries)


def test_legacy_preserves_category_previous_layout_and_not_submitted(nba_pdf_loader):
    content = nba_pdf_loader("legacy_category_v1")
    parsed = parse_report_pdf(content)

    assert len(parsed.entries) == 4
    assert parsed.entries[0].reason_category == "Injury/Illness"
    assert parsed.entries[0].raw_reason == "Injury/Illness - Left thumb sprain"
    assert parsed.entries[-1].entry_type == "not_submitted"
    assert parsed.entries[-1].player_name is None


def test_compact_handles_no_repeated_header_and_multiline_reasons(nba_pdf_loader):
    content = nba_pdf_loader("compact_v3")
    parsed = parse_report_pdf(content)

    assert len(parsed.entries) == 4
    assert parsed.entries[0].player_name == "Alexander-Walker, Nickeil"
    assert parsed.entries[0].raw_reason == "Injury/Illness - Right Great Toe; Sprain"
    assert parsed.entries[-1].page_number == 2
    assert parsed.entries[-1].raw_reason == "Injury/Illness - Left Ankle; Sprain"


def test_status_history_preserves_current_and_previous_values(nba_pdf_loader):
    content = nba_pdf_loader("legacy_status_history_v1b")
    parsed = parse_report_pdf(content)

    assert parsed.entries[0].status == "Probable"
    assert parsed.entries[0].raw_reason == "Injury/Illness - Left Shoulder; Contusion"
    assert parsed.entries[0].previous_status == "Questionable"
    assert parsed.entries[0].previous_reason == "Injury/Illness - Left Shoulder; Contusion"


def test_reason_first_transition_keeps_wrapped_reason_with_correct_player(nba_pdf_loader):
    parsed = parse_report_pdf(nba_pdf_loader("legacy_reason_first_v1c"))

    assert parsed.entries[1].player_name == "Batum, Nicolas"
    assert parsed.entries[1].status == "Out"
    assert parsed.entries[1].raw_reason == "Injury/Illness - Fractured Third Finger, Left Hand"


def test_current_previous_transition_does_not_mix_previous_status(nba_pdf_loader):
    parsed = parse_report_pdf(nba_pdf_loader("legacy_current_previous_v1d"))

    assert parsed.entries[1].status == "Out"
    assert parsed.entries[1].previous_status == "-"
    assert parsed.entries[1].raw_reason == "Injury/Illness - Left Thigh; Contusion"


def test_legacy_keeps_wrapped_minnesota_team_cell_with_neighboring_players(nba_pdf_loader):
    parsed = parse_report_pdf(nba_pdf_loader("legacy_structural_defects_v1"))

    rose = next(entry for entry in parsed.entries if entry.player_name == "Rose, Derrick")
    teague = next(entry for entry in parsed.entries if entry.player_name == "Teague, Jeff")
    assert rose.team == "Minnesota Timberwolves"
    assert teague.team == "Minnesota Timberwolves"
    assert rose.player_name == "Rose, Derrick"
    assert teague.player_name == "Teague, Jeff"
    assert "Minnesota" in rose.raw_row_text
    assert "Timberwolves" in rose.raw_row_text


def test_legacy_represents_all_players_available_as_its_own_team_observation(nba_pdf_loader):
    parsed = parse_report_pdf(nba_pdf_loader("legacy_structural_defects_v1"))

    assert len(parsed.entries) == 6
    aldridge = next(entry for entry in parsed.entries if entry.player_name == "Aldridge, LaMarcus")
    not_submitted = next(entry for entry in parsed.entries if entry.entry_type == "not_submitted")
    all_available = [entry for entry in parsed.entries if entry.entry_type == "all_available"]
    assert "ALL PLAYERS AVAILABLE" not in (aldridge.raw_reason or "")
    assert "ALL PLAYERS AVAILABLE" not in aldridge.raw_row_text
    assert not_submitted.entry_type == "not_submitted"
    assert not_submitted.team == "Dallas Mavericks"
    assert "ALL PLAYERS AVAILABLE" not in not_submitted.raw_row_text
    assert [entry.team for entry in all_available] == [
        "Phoenix Suns",
        "Portland Trail Blazers",
    ]
    assert all(entry.player_name is None for entry in all_available)
    assert all(entry.status is None for entry in all_available)
    assert all(entry.reason_category is None for entry in all_available)
    assert all(entry.raw_reason == "ALL PLAYERS AVAILABLE" for entry in all_available)
    assert all_available[-1].raw_row_text == "Portland Trail Blazers ALL PLAYERS AVAILABLE"


def test_rejects_non_pdf_and_changed_structure(nba_pdf_builder):
    with pytest.raises(NBAReportParseError, match="Not a PDF"):
        parse_report_pdf(b"not a pdf")
    changed = nba_pdf_builder([[[200, 550, "Injury Report: 04/12/26 05:00 PM"]]])
    with pytest.raises(NBAReportParseError, match="table header missing"):
        parse_report_pdf(changed)


def test_extract_date_matchups_returns_distinct_pairs(nba_pdf_loader):
    content = nba_pdf_loader("standard_v2")
    pairs = extract_date_matchups(content)

    assert pairs == {("04/18/2021", "IND@ATL")}


def test_extract_date_matchups_raises_on_not_submitted_only_pdf(nba_pdf_builder):
    """A PDF containing only NOT YET SUBMITTED rows has no real player matchups."""
    content = _build_injury_report(
        "04/12/26 05:00 PM",
        "04/12/2026",
        [{"HOU@LAL": [("NOT YET SUBMITTED", "NOT YET SUBMITTED")]}],
    )
    with pytest.raises(NBAReportParseError, match="No date/matchup pairs found"):
        extract_date_matchups(content)


def test_extract_date_matchups_raises_on_all_players_available_only_pdf(nba_pdf_builder):
    """A PDF containing only ALL PLAYERS AVAILABLE rows has no real player matchups."""
    page: list[list[object]] = [
        [270, 550, "Injury Report: 04/12/26 05:00 PM"],
        [20, 510, "Game Date"],
        [94, 510, "Game Time"],
        [169, 510, "Matchup"],
        [243, 510, "Team"],
        [370, 510, "Player Name"],
        [498, 510, "Current Status"],
        [604, 510, "Reason"],
        [20, 490, "04/12/2026"],
        [169, 490, "HOU@LAL"],
        [243, 490, "Houston Rockets"],
        [370, 490, "ALL PLAYERS AVAILABLE"],
    ]
    content = nba_pdf_builder([page])
    with pytest.raises(NBAReportParseError, match="No date/matchup pairs found"):
        extract_date_matchups(content)


def test_extract_date_matchups_ignores_non_player_rows(nba_pdf_builder):
    """Matchups with real player rows are returned; NOT YET SUBMITTED and
    ALL PLAYERS AVAILABLE rows in the same PDF do not produce extra matchups."""
    page: list[list[object]] = [
        [270, 550, "Injury Report: 04/12/26 05:00 PM"],
        [20, 510, "Game Date"],
        [94, 510, "Game Time"],
        [169, 510, "Matchup"],
        [243, 510, "Team"],
        [370, 510, "Player Name"],
        [498, 510, "Current Status"],
        [604, 510, "Reason"],
        [20, 490, "04/12/2026"],
        [169, 490, "BOS@MIL"],
        [243, 490, "Boston Celtics"],
        [370, 490, "Tatum, Jayson"],
        [498, 490, "Out"],
        [604, 490, "Injury/Illness"],
        [20, 465, "04/12/2026"],
        [169, 465, "HOU@LAL"],
        [243, 465, "Houston Rockets"],
        [370, 465, "NOT YET SUBMITTED"],
        [20, 440, "04/12/2026"],
        [169, 440, "PHI@GSW"],
        [243, 440, "Phoenix Suns"],
        [370, 440, "ALL PLAYERS AVAILABLE"],
    ]
    content = nba_pdf_builder([page])
    pairs = extract_date_matchups(content)
    assert pairs == {("04/12/2026", "BOS@MIL")}


def _build_injury_report(
    timestamp_str: str,
    game_date: str,
    games: list[dict[str, list[tuple[str, str]]]],
) -> bytes:
    """Build an injury-report PDF with player rows for select_latest_reports tests.

    *games* is a list of dicts mapping matchup to a list of
    ``(player_name, status)`` tuples.  A status of ``"NOT YET SUBMITTED"``
    produces the corresponding placeholder row instead of a real player entry.
    Column geometry matches the standard_v2 fixture layout.
    """
    page: list[list[object]] = [
        [270, 550, f"Injury Report: {timestamp_str}"],
        [20, 510, "Game Date"],
        [94, 510, "Game Time"],
        [169, 510, "Matchup"],
        [243, 510, "Team"],
        [370, 510, "Player Name"],
        [498, 510, "Current Status"],
        [604, 510, "Reason"],
    ]
    y = 490
    for game in games:
        for matchup, rows in game.items():
            team = matchup.split("@")[0]
            for player, status in rows:
                page.append([20, y, game_date])
                page.append([169, y, matchup])
                page.append([243, y, team])
                if status == "NOT YET SUBMITTED":
                    page.append([370, y, "NOT YET SUBMITTED"])
                else:
                    page.append([370, y, player])
                    page.append([498, y, status])
                    page.append([604, y, "Injury/Illness"])
                y -= 25
    return build_text_pdf([page])


def test_drops_earlier_pdf_when_later_pdf_covers_same_game(nba_pdf_builder):
    earlier = _build_injury_report(
        "04/12/26 05:00 PM", "04/12/2026",
        [{"BOS@MIL": [("Tatum, Jayson", "Out")]}],
    )
    later = _build_injury_report(
        "04/12/26 09:00 PM", "04/12/2026",
        [{"BOS@MIL": [("Tatum, Jayson", "Out")]}],
    )
    reports = [
        Report(content=earlier, timestamp=datetime(2026, 4, 12, 17, 0)),
        Report(content=later, timestamp=datetime(2026, 4, 12, 21, 0)),
    ]
    selected = select_latest_reports(reports)

    assert len(selected) == 1
    assert selected[0].content is later


def test_keeps_pdf_that_is_latest_for_another_game(nba_pdf_builder):
    early = _build_injury_report(
        "04/12/26 05:00 PM", "04/12/2026",
        [{"BOS@MIL": [("Tatum, Jayson", "Out")]}],
    )
    late = _build_injury_report(
        "04/12/26 09:00 PM", "04/12/2026",
        [
            {"BOS@MIL": [("Tatum, Jayson", "Out")]},
            {"LAL@GSW": [("Curry, Stephen", "Questionable")]},
        ],
    )
    reports = [
        Report(content=early, timestamp=datetime(2026, 4, 12, 17, 0)),
        Report(content=late, timestamp=datetime(2026, 4, 12, 21, 0)),
    ]
    selected = select_latest_reports(reports)

    assert len(selected) == 1
    assert selected[0].content is late


def test_early_pdf_not_selected_for_not_submitted_only_matchup():
    """An early PDF with PHI@BOS player rows plus HOU@LAL NOT YET SUBMITTED
    should not be retained when a later PDF supersedes PHI@BOS."""
    early = _build_injury_report(
        "04/12/26 05:00 PM",
        "04/12/2026",
        [
            {"PHI@BOS": [("Embiid, Joel", "Out")]},
            {"HOU@LAL": [("NOT YET SUBMITTED", "NOT YET SUBMITTED")]},
        ],
    )
    later = _build_injury_report(
        "04/12/26 09:00 PM",
        "04/12/2026",
        [
            {"PHI@BOS": [("Embiid, Joel", "Out")]},
        ],
    )
    reports = [
        Report(content=early, timestamp=datetime(2026, 4, 12, 17, 0)),
        Report(content=later, timestamp=datetime(2026, 4, 12, 21, 0)),
    ]
    selected = select_latest_reports(reports)

    assert len(selected) == 1
    assert selected[0].content is later


def test_select_latest_reports_from_pairs_matches_select_latest_reports():
    """select_latest_reports_from_pairs should produce the same result as
    select_latest_reports when given pre-extracted pairs."""
    earlier = _build_injury_report(
        "04/12/26 05:00 PM", "04/12/2026",
        [{"BOS@MIL": [("Tatum, Jayson", "Out")]}],
    )
    later = _build_injury_report(
        "04/12/26 09:00 PM", "04/12/2026",
        [{"BOS@MIL": [("Tatum, Jayson", "Out")]}],
    )
    reports = [
        Report(content=earlier, timestamp=datetime(2026, 4, 12, 17, 0)),
        Report(content=later, timestamp=datetime(2026, 4, 12, 21, 0)),
    ]

    report_pairs = [(r, extract_date_matchups(r.content)) for r in reports]
    selected_from_pairs = select_latest_reports_from_pairs(report_pairs)
    selected_from_reports = select_latest_reports(reports)

    assert [r.content for r in selected_from_pairs] == [
        r.content for r in selected_from_reports
    ]
    assert len(selected_from_pairs) == 1
    assert selected_from_pairs[0].content is later
