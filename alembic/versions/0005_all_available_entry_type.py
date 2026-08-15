"""Represent all-players-available source rows as team observations.

Revision ID: 0005_all_available_entry_type
Revises: 0004_candidate_report_lineage
Create Date: 2026-08-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_all_available_entry_type"
down_revision: str | None = "0004_candidate_report_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("entry_type_allowed", "nba_report_entries", type_="check")
    op.create_check_constraint(
        "entry_type_allowed",
        "nba_report_entries",
        "entry_type IN ('player', 'not_submitted', 'all_available')",
    )


def downgrade() -> None:
    op.drop_constraint("entry_type_allowed", "nba_report_entries", type_="check")
    op.create_check_constraint(
        "entry_type_allowed",
        "nba_report_entries",
        "entry_type IN ('player', 'not_submitted')",
    )
