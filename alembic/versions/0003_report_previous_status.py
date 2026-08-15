"""Preserve previous status and reason from 2019 status-history reports.

Revision ID: 0003_report_previous_status
Revises: 0002_nba_official_reports
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_report_previous_status"
down_revision: str | None = "0002_nba_official_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("nba_report_entries", sa.Column("previous_status", sa.String(64)))
    op.add_column("nba_report_entries", sa.Column("previous_reason", sa.Text()))


def downgrade() -> None:
    op.drop_column("nba_report_entries", "previous_reason")
    op.drop_column("nba_report_entries", "previous_status")
