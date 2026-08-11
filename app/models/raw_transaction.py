from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.types import BIGINT


class RawTransaction(Base):
    __tablename__ = "raw_transactions"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('il', 'missed_game')",
            name="source_type_allowed",
        ),
        UniqueConstraint(
            "source_type",
            "source_row_key",
            name="uq_raw_transactions_source_type_source_row_key",
        ),
        Index("ix_raw_transactions_date", "transaction_date"),
        Index("ix_raw_transactions_relinquished", "relinquished"),
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    team: Mapped[str | None] = mapped_column(Text)
    acquired: Mapped[str | None] = mapped_column(Text)
    relinquished: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_row_key: Mapped[str] = mapped_column(Text, nullable=False)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
