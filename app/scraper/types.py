from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

type SourceType = Literal["il", "missed_game"]


@dataclass(frozen=True)
class ScrapedTransaction:
    source_type: SourceType
    transaction_date: date
    team: str
    acquired: str
    relinquished: str
    notes: str
    source_url: str


@dataclass(frozen=True)
class ParsedResultsPage:
    records: list[ScrapedTransaction]
    next_url: str | None
