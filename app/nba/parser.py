from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import datetime
from io import BytesIO
from typing import Any, NamedTuple

import pdfplumber

from app.nba.types import ParsedNBAReport, ParsedNBAReportEntry


class Report(NamedTuple):
    """A downloaded PDF with its report timestamp, used by :func:`select_latest_reports`."""

    content: bytes
    timestamp: datetime

PARSER_VERSION = "nba-pdf-v5"


class NBAReportParseError(ValueError):
    """Raised when an NBA report no longer matches a supported tabular structure."""


_REPORT_TIMESTAMP_RE = re.compile(
    r"Injury\s*Report:\s*(\d{2}/\d{2}/\d{2})\s+(\d{1,2}:\d{2})\s*([AP]M)",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_TIME_RE = re.compile(r"^(\d{1,2}:\d{2})(?:\s*\(ET\))?$")
_MATCHUP_RE = re.compile(r"^[A-Z]{2,3}@[A-Z]{2,3}$")
_KNOWN_STATUSES = {"Available", "Probable", "Questionable", "Doubtful", "Out"}
_ALL_PLAYERS_AVAILABLE = "ALL PLAYERS AVAILABLE"
_TEAM_WRAP_MAX_DISTANCE = 10.0


def extract_date_matchups(content: bytes) -> set[tuple[str, str]]:
    """Return the distinct (game_date, matchup) pairs found in an NBA PDF report.

    A matchup is only considered present if the PDF contains at least one actual
    player/status row for that game.  Games whose teams are only marked
    "NOT YET SUBMITTED" or "ALL PLAYERS AVAILABLE" are ignored so that
    :func:`select_latest_reports` does not retain an early PDF solely because
    of an unsourced matchup that a later PDF never covers.

    This is a lightweight extractor that uses pdfplumber ``extract_words()``
    and existing column-detection regexes without building
    :class:`~app.nba.types.ParsedNBAReportEntry` objects.
    """
    if not content.startswith(b"%PDF-"):
        raise NBAReportParseError("Not a PDF")

    try:
        pdf = pdfplumber.open(BytesIO(content))
    except Exception as exc:
        raise NBAReportParseError("Unreadable PDF") from exc

    with pdf:
        found: set[tuple[str, str]] = set()
        context: dict[str, str] = {"date": "", "time": "", "matchup": "", "team": ""}
        prior_columns: list[tuple[str, float]] | None = None

        for page in pdf.pages:
            lines = _group_lines(page.extract_words(x_tolerance=1, y_tolerance=2))

            header_index = None
            columns = prior_columns
            for index, line in enumerate(lines):
                header = _header_columns(line)
                if header:
                    header_index = index
                    _, columns = header
                    prior_columns = columns
                    break
            if columns is None:
                continue

            data_lines = lines[header_index + 1:] if header_index is not None else lines

            ordered = sorted(columns, key=lambda pair: pair[1])
            player_x = next((x for name, x in columns if name == "player"), None)
            status_x = next((x for name, x in columns if name == "status"), None)
            if player_x is None or status_x is None:
                continue

            for line in data_lines:
                raw_line = _line_text(line)
                if (
                    not raw_line
                    or re.fullmatch(r"Page \d+ of \d+", raw_line)
                    or _REPORT_TIMESTAMP_RE.search(raw_line)
                ):
                    continue

                values: dict[str, str] = {name: "" for name, _ in columns}
                for word in sorted(line, key=lambda item: item["x0"]):
                    idx = 0
                    for pos in range(1, len(ordered)):
                        if word["x0"] >= ordered[pos][1]:
                            idx = pos
                        else:
                            break
                    values[ordered[idx][0]] = (
                        _normalize(f"{values[ordered[idx][0]]} {word['text']}")
                        if values[ordered[idx][0]]
                        else word["text"]
                    )

                for field in ("date", "time", "matchup", "team"):
                    candidate = values.get(field, "")
                    if candidate and (
                        (field == "date" and _DATE_RE.match(candidate))
                        or (field == "time" and _TIME_RE.match(candidate))
                        or (field == "matchup" and _MATCHUP_RE.match(candidate))
                        or field == "team"
                    ):
                        context[field] = candidate

                player_val = values.get("player", "")
                status_val = values.get("status", "")
                has_player_and_status = bool(player_val and status_val)
                is_not_submitted = "NOT YET SUBMITTED" in raw_line.upper()
                is_all_available = _ALL_PLAYERS_AVAILABLE in raw_line.upper()

                if has_player_and_status and not is_not_submitted and not is_all_available:
                    if context["date"] and context["matchup"]:
                        found.add((context["date"], context["matchup"]))

        if not found:
            raise NBAReportParseError("No date/matchup pairs found")
        return found


def select_latest_reports(
    reports: list[Report],
) -> list[Report]:
    """Select the latest PDF for each distinct game across multiple reports.

    Uses :func:`extract_date_matchups` to discover which games each PDF covers
    (only matchups with at least one actual player row).  A PDF may cover
    multiple games; the latest report timestamp wins per game.  Returns only
    the unique selected reports in their original order.
    """
    report_pairs: list[tuple[Report, set[tuple[str, str]]]] = []
    for report in reports:
        try:
            game_pairs = extract_date_matchups(report.content)
        except NBAReportParseError:
            logging.warning(
                "extract_date_matchups failed for report with timestamp %s, skipping",
                report.timestamp.isoformat(),
            )
            continue
        report_pairs.append((report, game_pairs))
    return select_latest_reports_from_pairs(report_pairs)


def select_latest_reports_from_pairs(
    report_pairs: list[tuple[Report, set[tuple[str, str]]]],
) -> list[Report]:
    """Select the latest PDF for each distinct game from pre-extracted pairs.

    Behaves identically to :func:`select_latest_reports` but accepts
    ``report_pairs`` so that callers who have already called
    :func:`extract_date_matchups` avoid a redundant second parse.
    """
    if not report_pairs:
        return []

    game_latest: dict[tuple[str, str], Report] = {}
    for report, game_pairs in report_pairs:
        for pair in game_pairs:
            existing = game_latest.get(pair)
            if existing is None or report.timestamp > existing.timestamp:
                game_latest[pair] = report

    seen: set[int] = set()
    selected: list[Report] = []
    for report, _ in report_pairs:
        report_id = id(report)
        if report_id in seen:
            continue
        if any(game_latest[pair] is report for pair in game_latest):
            seen.add(report_id)
            selected.append(report)
    return selected


def _normalize(value: str | None) -> str:
    return " ".join((value or "").split())


def _group_lines(words: list[dict[str, Any]], tolerance: float = 2.2) -> list[list[dict[str, Any]]]:
    lines: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if not lines or abs(word["top"] - lines[-1][0]["top"]) > tolerance:
            lines.append([word])
        else:
            lines[-1].append(word)
    return lines


def _line_text(words: Iterable[dict[str, Any]]) -> str:
    return _normalize(" ".join(word["text"] for word in sorted(words, key=lambda item: item["x0"])))


def _header_columns(line: list[dict[str, Any]]) -> tuple[str, list[tuple[str, float]]] | None:
    text = _line_text(line)
    if "Game Date" not in text or "Player Name" not in text:
        return None

    game_words = [word for word in line if word["text"] == "Game"]
    lookup = {word["text"]: word for word in line}
    try:
        common = [
            ("date", game_words[0]["x0"] - 2),
            ("time", game_words[1]["x0"] - 2),
            ("matchup", lookup["Matchup"]["x0"] - 2),
            ("team", lookup["Team"]["x0"] - 2),
            ("player", lookup["Player"]["x0"] - 2),
        ]
        if "Category" in lookup:
            columns = common + [
                ("category", lookup["Category"]["x0"] - 2),
                ("reason", lookup["Reason"]["x0"] - 2),
                ("status", lookup["Current"]["x0"] - 2),
                ("previous_status", lookup["Previous"]["x0"] - 2),
            ]
            return "legacy-category-v1", columns
        previous_words = [word for word in line if word["text"] == "Previous"]
        reason_words = [word for word in line if word["text"] == "Reason"]
        if previous_words:
            if len(reason_words) == 2 and len(previous_words) == 2:
                columns = common + [
                    ("status", lookup["Current"]["x0"] - 2),
                    ("reason", reason_words[0]["x0"] - 2),
                    ("previous_status", previous_words[0]["x0"] - 2),
                    ("previous_reason", previous_words[1]["x0"] - 2),
                ]
                return "legacy-status-history-v1b", columns
            if len(reason_words) == 1 and len(previous_words) == 1:
                status_x = lookup["Current"]["x0"] - 2
                reason_x = reason_words[0]["x0"] - 2
                columns = common + [
                    ("status", status_x),
                    ("reason", reason_x),
                    ("previous_status", previous_words[0]["x0"] - 2),
                ]
                version = (
                    "legacy-reason-first-v1c"
                    if reason_x < status_x
                    else "legacy-current-previous-v1d"
                )
                return version, columns
            raise NBAReportParseError(f"Incomplete status-history header: {text}")
        columns = common + [
            ("status", lookup["Current"]["x0"] - 2),
            ("reason", lookup["Reason"]["x0"] - 2),
        ]
        # In 2024 the NBA changed generators, font sizing, and column geometry.
        version = "compact-v3" if lookup["Player"]["x0"] > 400 else "standard-v2"
        return version, columns
    except (IndexError, KeyError) as exc:
        raise NBAReportParseError(f"Incomplete NBA report header: {text}") from exc


def _assign_columns(line: list[dict[str, Any]], columns: list[tuple[str, float]]) -> dict[str, str]:
    values: dict[str, list[str]] = {name: [] for name, _ in columns}
    ordered = sorted(columns, key=lambda pair: pair[1])
    for word in sorted(line, key=lambda item: item["x0"]):
        index = 0
        for possible in range(1, len(ordered)):
            # NBA PDFs align each cell at the header's x-coordinate. Use the next
            # column start as the boundary; a midpoint truncates long player names.
            if word["x0"] >= ordered[possible][1]:
                index = possible
            else:
                break
        values[ordered[index][0]].append(word["text"])
    return {name: _normalize(" ".join(parts)) for name, parts in values.items()}


def _parse_reason(reason: str, category: str) -> tuple[str | None, str | None]:
    reason = _normalize(reason)
    category = _normalize(category)
    if category:
        combined = _normalize(f"{category} - {reason}" if reason else category)
        return category, combined
    if " - " in reason:
        prefix, _ = reason.split(" - ", 1)
        if prefix in {"Injury/Illness", "G League", "League Suspension"}:
            return prefix, reason
    return None, reason or None


def parse_report_pdf(content: bytes, *, source_url: str = "<bytes>") -> ParsedNBAReport:
    """Parse a text-native official NBA injury report using its column coordinates."""

    if not content.startswith(b"%PDF-"):
        raise NBAReportParseError(f"Not a PDF: {source_url}")

    try:
        pdf = pdfplumber.open(BytesIO(content))
    except Exception as exc:
        raise NBAReportParseError(f"Unreadable PDF: {source_url}") from exc

    with pdf:
        page_lines = [
            _group_lines(page.extract_words(x_tolerance=1, y_tolerance=2)) for page in pdf.pages
        ]
        page_texts = ["\n".join(_line_text(line) for line in lines) for lines in page_lines]
        raw_text = "\n\f\n".join(page_texts)
        timestamp_match = _REPORT_TIMESTAMP_RE.search(raw_text)
        if not timestamp_match:
            raise NBAReportParseError(f"Missing report timestamp: {source_url}")
        report_timestamp = datetime.strptime(
            " ".join(timestamp_match.groups()), "%m/%d/%y %I:%M %p"
        )

        entries: list[ParsedNBAReportEntry] = []
        context: dict[str, str] = {"date": "", "time": "", "matchup": "", "team": ""}
        pending: dict[str, Any] | None = None
        deferred: list[tuple[int, float, dict[str, str], str]] = []
        found_formats: set[str] = set()
        prior_header: tuple[str, list[tuple[str, float]]] | None = None

        def append_fragments(target: dict[str, Any], values: dict[str, str], raw_line: str) -> None:
            if values.get("player") and not values.get("status") and not values.get("reason"):
                target["player"] = _normalize(f"{target['player']} {values['player']}")
            if values.get("category"):
                target["category"] = _normalize(
                    f"{target.get('category', '')} {values['category']}"
                )
            if values.get("reason"):
                target["reason"] = _normalize(f"{target.get('reason', '')} {values['reason']}")
            if values.get("previous_reason"):
                target["previous_reason"] = _normalize(
                    f"{target.get('previous_reason', '')} {values['previous_reason']}"
                )
            target["raw_lines"].append(raw_line)

        def finish_pending() -> None:
            nonlocal pending
            if pending is None:
                return
            category, raw_reason = _parse_reason(
                pending.get("reason", ""), pending.get("category", "")
            )
            try:
                game_date = datetime.strptime(pending["date"], "%m/%d/%Y").date()
            except (KeyError, ValueError) as exc:
                raise NBAReportParseError(
                    f"Entry without a valid game date in {source_url}: {pending}"
                ) from exc
            time_match = _TIME_RE.match(pending.get("time", ""))
            game_time = (
                datetime.strptime(time_match.group(1), "%H:%M").time() if time_match else None
            )
            player = _normalize(pending.get("player")) or None
            reason_text = _normalize(raw_reason)
            all_available = (
                _ALL_PLAYERS_AVAILABLE
                in " ".join(
                    [
                        player or "",
                        pending.get("status", ""),
                        pending.get("category", ""),
                        reason_text,
                    ]
                ).upper()
            )
            not_submitted = (
                "NOT YET SUBMITTED"
                in " ".join([player or "", pending.get("status", ""), reason_text]).upper()
            )
            entry_type = (
                "all_available" if all_available else "not_submitted" if not_submitted else "player"
            )
            if entry_type == "player" and not player:
                pending = None
                return
            status = _normalize(pending.get("status")) or None
            if entry_type == "player" and status not in _KNOWN_STATUSES:
                raise NBAReportParseError(
                    f"Unsupported or misaligned status {status!r} in {source_url}: {pending}"
                )
            if entry_type == "all_available":
                category = None
                raw_reason = _ALL_PLAYERS_AVAILABLE
                status = None
            entries.append(
                ParsedNBAReportEntry(
                    page_number=pending["page_number"],
                    row_number=len(entries) + 1,
                    game_date=game_date,
                    game_time=game_time,
                    matchup=pending["matchup"],
                    team=pending["team"],
                    player_name=player if entry_type == "player" else None,
                    status=status,
                    reason_category=category,
                    raw_reason=raw_reason,
                    previous_status=_normalize(pending.get("previous_status")) or None,
                    previous_reason=_normalize(pending.get("previous_reason")) or None,
                    raw_row_text=_normalize(" ".join(pending["raw_lines"])),
                    entry_type=entry_type,
                )
            )
            pending = None

        for page_number, lines in enumerate(page_lines, start=1):
            header_index = None
            header: tuple[str, list[tuple[str, float]]] | None = None
            for index, line in enumerate(lines):
                header = _header_columns(line)
                if header:
                    header_index = index
                    break
            if header is None or header_index is None:
                if page_number == 1 or prior_header is None:
                    raise NBAReportParseError(
                        f"Expected report table header missing on page {page_number}: {source_url}"
                    )
                # The compact 2024+ generator repeats the title but not the column header.
                header = prior_header
                data_lines = lines
            else:
                prior_header = header
                data_lines = lines[header_index + 1 :]
            format_version, columns = header
            found_formats.add(format_version)

            for line in data_lines:
                raw_line = _line_text(line)
                if (
                    not raw_line
                    or re.fullmatch(r"Page \d+ of \d+", raw_line)
                    or _REPORT_TIMESTAMP_RE.search(raw_line)
                ):
                    continue
                values = _assign_columns(line, columns)
                line_y = float(line[0]["top"])
                is_wrapped_team_continuation = (
                    pending is not None
                    and pending["page_number"] == page_number
                    and bool(values.get("team"))
                    and all(not values.get(field) for field in values if field != "team")
                    and 0 < line_y - pending["anchor_y"] <= _TEAM_WRAP_MAX_DISTANCE
                )
                if is_wrapped_team_continuation:
                    combined_team = _normalize(f"{pending['team']} {values['team']}")
                    pending["team"] = combined_team
                    context["team"] = combined_team
                    pending["raw_lines"].append(raw_line)
                    continue
                for field in ("date", "time", "matchup", "team"):
                    if values.get(field):
                        candidate = values[field]
                        valid = (
                            (field == "date" and _DATE_RE.match(candidate))
                            or (field == "time" and _TIME_RE.match(candidate))
                            or (field == "matchup" and _MATCHUP_RE.match(candidate))
                            or field == "team"
                        )
                        if valid:
                            context[field] = candidate

                player = values.get("player", "")
                status = values.get("status", "")
                reason = values.get("reason", "")
                is_not_submitted = "NOT YET SUBMITTED" in raw_line.upper()
                is_all_available = _ALL_PLAYERS_AVAILABLE in raw_line.upper()
                begins_entry = bool(player and status) or is_not_submitted or is_all_available

                if begins_entry:
                    leading: list[tuple[dict[str, str], str]] = []
                    anchor_y = float(line[0]["top"])
                    for deferred_page, deferred_y, deferred_values, deferred_raw in deferred:
                        previous_distance = float("inf")
                        if pending is not None and pending["page_number"] == deferred_page:
                            previous_distance = abs(deferred_y - pending["anchor_y"])
                        next_distance = (
                            abs(deferred_y - anchor_y)
                            if deferred_page == page_number
                            else float("inf")
                        )
                        if pending is not None and previous_distance <= next_distance:
                            append_fragments(pending, deferred_values, deferred_raw)
                        else:
                            leading.append((deferred_values, deferred_raw))
                    deferred.clear()
                    finish_pending()
                    if not all(context[field] for field in ("date", "matchup", "team")):
                        raise NBAReportParseError(
                            f"Entry missing inherited game/team context on page {page_number}: "
                            f"{raw_line}"
                        )
                    pending = {
                        **context,
                        "player": player,
                        "status": status,
                        "category": values.get("category", ""),
                        "reason": reason,
                        "previous_status": values.get("previous_status", ""),
                        "previous_reason": values.get("previous_reason", ""),
                        "page_number": page_number,
                        "anchor_y": anchor_y,
                        "raw_lines": [raw_line],
                    }
                    for leading_values, leading_raw in leading:
                        append_fragments(pending, leading_values, leading_raw)
                elif pending is not None:
                    deferred.append((page_number, float(line[0]["top"]), values, raw_line))
                elif reason or values.get("category"):
                    deferred.append((page_number, float(line[0]["top"]), values, raw_line))

        for _, _, deferred_values, deferred_raw in deferred:
            if pending is not None:
                append_fragments(pending, deferred_values, deferred_raw)
        finish_pending()

    if not entries:
        raise NBAReportParseError(f"No report entries found: {source_url}")
    if len(found_formats) != 1:
        raise NBAReportParseError(f"Mixed report formats in {source_url}: {sorted(found_formats)}")
    return ParsedNBAReport(
        report_date=report_timestamp.date(),
        report_time=report_timestamp.time(),
        format_version=found_formats.pop(),
        parser_version=PARSER_VERSION,
        raw_text=raw_text,
        entries=tuple(entries),
    )
