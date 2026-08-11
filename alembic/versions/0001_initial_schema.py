"""Create raw transactions, update runs, and injuries.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "raw_transactions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("team", sa.Text(), nullable=True),
        sa.Column("acquired", sa.Text(), nullable=True),
        sa.Column("relinquished", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_row_key", sa.Text(), nullable=False),
        sa.Column(
            "scraped_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_type IN ('il', 'missed_game')",
            name=op.f("ck_raw_transactions_source_type_allowed"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_raw_transactions"),
        sa.UniqueConstraint(
            "source_type",
            "source_row_key",
            name="uq_raw_transactions_source_type_source_row_key",
        ),
    )
    op.create_index(
        "ix_raw_transactions_date",
        "raw_transactions",
        ["transaction_date"],
        unique=False,
    )
    op.create_index(
        "ix_raw_transactions_relinquished",
        "raw_transactions",
        ["relinquished"],
        unique=False,
    )

    op.create_table(
        "update_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_start_date", sa.Date(), nullable=True),
        sa.Column("requested_end_date", sa.Date(), nullable=True),
        sa.Column("rows_fetched", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_inserted", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_processed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_details", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_update_runs"),
    )

    op.create_table(
        "injuries",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("season", sa.String(length=7), nullable=False),
        sa.Column("player_name", sa.Text(), nullable=False),
        sa.Column("team", sa.Text(), nullable=True),
        sa.Column("body_part", sa.Text(), nullable=True),
        sa.Column("injury_type", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("preferred_source", sa.String(length=32), nullable=True),
        sa.Column("source_raw_transaction_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_raw_transaction_id"],
            ["raw_transactions.id"],
            name="fk_injuries_source_raw_transaction_id_raw_transactions",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_injuries"),
    )
    op.create_index("ix_injuries_date", "injuries", ["date"], unique=False)
    op.create_index("ix_injuries_player_name", "injuries", ["player_name"], unique=False)
    op.create_index("ix_injuries_team", "injuries", ["team"], unique=False)
    op.create_index("ix_injuries_season", "injuries", ["season"], unique=False)
    op.create_index("ix_injuries_body_part", "injuries", ["body_part"], unique=False)
    op.create_index("ix_injuries_injury_type", "injuries", ["injury_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_injuries_injury_type", table_name="injuries")
    op.drop_index("ix_injuries_body_part", table_name="injuries")
    op.drop_index("ix_injuries_season", table_name="injuries")
    op.drop_index("ix_injuries_team", table_name="injuries")
    op.drop_index("ix_injuries_player_name", table_name="injuries")
    op.drop_index("ix_injuries_date", table_name="injuries")
    op.drop_table("injuries")
    op.drop_table("update_runs")
    op.drop_index("ix_raw_transactions_relinquished", table_name="raw_transactions")
    op.drop_index("ix_raw_transactions_date", table_name="raw_transactions")
    op.drop_table("raw_transactions")
