"""Link every downloaded candidate URL to its resolved report document.

Revision ID: 0004_candidate_report_lineage
Revises: 0003_report_previous_status
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_candidate_report_lineage"
down_revision: str | None = "0003_report_previous_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "nba_report_candidates", sa.Column("resolved_report_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        "fk_nba_report_candidates_resolved_report",
        "nba_report_candidates",
        "nba_reports",
        ["resolved_report_id"],
        ["id"],
    )
    op.create_index(
        "ix_nba_report_candidates_resolved_report",
        "nba_report_candidates",
        ["resolved_report_id"],
        unique=False,
    )
    op.execute(
        "UPDATE nba_report_candidates AS candidate "
        "SET resolved_report_id = report.id "
        "FROM nba_reports AS report "
        "WHERE report.candidate_id = candidate.id"
    )
    op.execute(
        "UPDATE nba_report_candidates "
        "SET resolved_report_id = "
        "substring(last_error FROM 'Content-identical to NBAReport ([0-9]+)')::bigint "
        "WHERE resolved_report_id IS NULL "
        "AND last_error ~ '^Content-identical to NBAReport [0-9]+'"
    )


def downgrade() -> None:
    op.drop_index("ix_nba_report_candidates_resolved_report", table_name="nba_report_candidates")
    op.drop_constraint(
        "fk_nba_report_candidates_resolved_report",
        "nba_report_candidates",
        type_="foreignkey",
    )
    op.drop_column("nba_report_candidates", "resolved_report_id")
