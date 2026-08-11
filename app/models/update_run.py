from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.types import BIGINT


class UpdateRun(Base):
    __tablename__ = "update_runs"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_start_date: Mapped[date | None] = mapped_column(Date)
    requested_end_date: Mapped[date | None] = mapped_column(Date)
    rows_fetched: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    rows_inserted: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    rows_processed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_details: Mapped[str | None] = mapped_column(Text)
