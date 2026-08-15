from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time


@dataclass(frozen=True)
class DiscoveredReport:
    source_url: str
    report_date: date
    report_time: time | None
    discovery_source_url: str


@dataclass(frozen=True)
class DownloadedReport:
    source_url: str
    content: bytes
    content_type: str
    downloaded_at: datetime
    content_hash: str


@dataclass(frozen=True)
class ParsedNBAReportEntry:
    page_number: int
    row_number: int
    game_date: date
    game_time: time | None
    matchup: str
    team: str
    player_name: str | None
    status: str | None
    reason_category: str | None
    raw_reason: str | None
    previous_status: str | None
    previous_reason: str | None
    raw_row_text: str
    entry_type: str = "player"


@dataclass(frozen=True)
class ParsedNBAReport:
    report_date: date
    report_time: time
    format_version: str
    parser_version: str
    raw_text: str
    entries: tuple[ParsedNBAReportEntry, ...]


@dataclass(frozen=True)
class ClassifiedReason:
    body_part: str | None
    laterality: str | None
    injury_type: str | None
    normalized_reason: str
    is_injury: bool
    classification_version: str
