from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import NBAReport, NBAReportCandidate
from app.nba.client import (
    NBAReportClient,
    NBAReportHTTPError,
    NBAReportMissingError,
    NBAReportValidationError,
)
from app.nba.parser import PARSER_VERSION, NBAReportParseError, parse_report_pdf
from app.nba.repository import EntityResolver, persist_parsed_report, store_discoveries
from app.nba.types import DiscoveredReport

logger = logging.getLogger(__name__)


@dataclass
class BackfillResult:
    discovered: int = 0
    candidates_inserted: int = 0
    already_known: int = 0
    downloaded: int = 0
    missing: int = 0
    http_failures: int = 0
    invalid_pdfs: int = 0
    parsed: int = 0
    parse_failures: int = 0
    entries_inserted: int = 0


class NBAReportBackfill:
    def __init__(self, session: Session, client: NBAReportClient) -> None:
        self.session = session
        self.client = client
        self.resolver = EntityResolver(session)

    def register(self, reports: list[DiscoveredReport]) -> BackfillResult:
        inserted, existing = store_discoveries(self.session, reports)
        self.session.commit()
        return BackfillResult(
            discovered=len(reports), candidates_inserted=inserted, already_known=existing
        )

    def run(
        self,
        start_date: date,
        end_date: date,
        *,
        retry_failures: bool = False,
        limit: int | None = None,
    ) -> BackfillResult:
        statuses = ["discovered"]
        if retry_failures:
            statuses.extend(["http_failed", "invalid_pdf", "parse_failed"])
        reconciled = self.session.execute(
            update(NBAReportCandidate)
            .where(
                NBAReportCandidate.status != "parsed",
                NBAReportCandidate.id.in_(
                    select(NBAReport.candidate_id).where(
                        NBAReport.parse_status == "parsed",
                        NBAReport.parser_version == PARSER_VERSION,
                    )
                ),
            )
            .values(status="parsed", last_error=None)
        ).rowcount
        self.session.commit()
        candidate_ids = list(
            self.session.scalars(
                select(NBAReportCandidate.id)
                .where(
                    NBAReportCandidate.report_date >= start_date,
                    NBAReportCandidate.report_date <= end_date,
                    NBAReportCandidate.status.in_(statuses),
                )
                .order_by(NBAReportCandidate.report_date, NBAReportCandidate.report_time)
                .limit(limit)
            )
        )
        result = BackfillResult(
            discovered=len(candidate_ids), already_known=max(reconciled or 0, 0)
        )
        for index, requested_candidate_id in enumerate(candidate_ids, start=1):
            candidate = self.session.scalar(
                select(NBAReportCandidate)
                .where(
                    NBAReportCandidate.id == requested_candidate_id,
                    NBAReportCandidate.status.in_(statuses),
                )
                .with_for_update(skip_locked=True)
            )
            if candidate is None:
                # A concurrent/resumed worker either owns this row or completed it after the
                # initial ID snapshot. Its committed state is authoritative.
                result.already_known += 1
                self.session.rollback()
                continue
            candidate_id = candidate.id
            report: NBAReport | None = None
            candidate.attempt_count += 1
            candidate.last_attempted_at = datetime.now(UTC)
            try:
                saved_report = self.session.scalar(
                    select(NBAReport).where(NBAReport.candidate_id == candidate.id)
                )
                if saved_report is not None:
                    candidate.resolved_report_id = saved_report.id
                    if (
                        saved_report.parse_status == "parsed"
                        and saved_report.parser_version == PARSER_VERSION
                    ):
                        candidate.status = "parsed"
                        candidate.last_error = None
                        result.already_known += 1
                        self.session.commit()
                        continue
                    report = saved_report
                    parsed = parse_report_pdf(
                        saved_report.content, source_url=saved_report.source_url
                    )
                    result.entries_inserted += persist_parsed_report(
                        self.session, saved_report, parsed, resolver=self.resolver
                    )
                    candidate.status = "parsed"
                    candidate.last_error = None
                    result.parsed += 1
                    self.session.commit()
                    continue
                downloaded = self.client.download(candidate.source_url)
                result.downloaded += 1
                existing_report = self.session.scalar(
                    select(NBAReport).where(NBAReport.content_hash == downloaded.content_hash)
                )
                if existing_report is not None:
                    candidate.resolved_report_id = existing_report.id
                    candidate.status = (
                        "parsed" if existing_report.parse_status == "parsed" else "downloaded"
                    )
                    candidate.last_error = (
                        f"Content-identical to NBAReport {existing_report.id}; source URL retained "
                        "on this candidate"
                    )
                    result.already_known += 1
                    self.session.commit()
                    continue
                report = NBAReport(
                    candidate_id=candidate.id,
                    report_date=candidate.report_date,
                    report_time=candidate.report_time,
                    source_url=candidate.source_url,
                    content_hash=downloaded.content_hash,
                    content=downloaded.content,
                    content_type=downloaded.content_type,
                    byte_length=len(downloaded.content),
                    downloaded_at=downloaded.downloaded_at,
                    parse_status="pending",
                )
                self.session.add(report)
                self.session.flush()
                candidate.resolved_report_id = report.id
                candidate.status = "downloaded"
                parsed = parse_report_pdf(downloaded.content, source_url=candidate.source_url)
                result.entries_inserted += persist_parsed_report(
                    self.session, report, parsed, resolver=self.resolver
                )
                candidate.status = "parsed"
                candidate.last_error = None
                result.parsed += 1
            except NBAReportMissingError as exc:
                candidate.status = "missing"
                candidate.last_error = str(exc)
                result.missing += 1
            except NBAReportValidationError as exc:
                candidate.status = "invalid_pdf"
                candidate.last_error = str(exc)
                result.invalid_pdfs += 1
            except NBAReportHTTPError as exc:
                candidate.status = "http_failed"
                candidate.last_error = str(exc)
                result.http_failures += 1
            except NBAReportParseError as exc:
                candidate.status = "parse_failed"
                candidate.last_error = str(exc)
                if report is not None:
                    report.parse_status = "failed"
                    report.parse_error = str(exc)
                result.parse_failures += 1
            except Exception as exc:
                # Preserve prior checkpoints even when persistence itself rejects a
                # malformed parsed value. The candidate remains explicitly retryable.
                self.session.rollback()
                candidate = self.session.get(NBAReportCandidate, candidate_id)
                if candidate is None:
                    raise
                candidate.attempt_count += 1
                candidate.last_attempted_at = datetime.now(UTC)
                candidate.status = "parse_failed"
                candidate.last_error = f"{type(exc).__name__}: {exc}"
                result.parse_failures += 1
                self.resolver = EntityResolver(self.session)
            self.session.commit()
            if index % 100 == 0 or index == len(candidate_ids):
                logger.info(
                    "NBA backfill checkpoint processed=%s/%s parsed=%s missing=%s "
                    "http_failed=%s invalid_pdf=%s parse_failed=%s",
                    index,
                    len(candidate_ids),
                    result.parsed,
                    result.missing,
                    result.http_failures,
                    result.invalid_pdfs,
                    result.parse_failures,
                )
        return result
