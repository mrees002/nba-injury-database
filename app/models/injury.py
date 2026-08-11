from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.types import BIGINT


class Injury(Base):
    __tablename__ = "injuries"
    __table_args__ = (
        Index("ix_injuries_date", "date"),
        Index("ix_injuries_player_name", "player_name"),
        Index("ix_injuries_team", "team"),
        Index("ix_injuries_season", "season"),
        Index("ix_injuries_body_part", "body_part"),
        Index("ix_injuries_injury_type", "injury_type"),
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    season: Mapped[str] = mapped_column(String(7), nullable=False)
    player_name: Mapped[str] = mapped_column(Text, nullable=False)
    team: Mapped[str | None] = mapped_column(Text)
    body_part: Mapped[str | None] = mapped_column(Text)
    injury_type: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    preferred_source: Mapped[str | None] = mapped_column(String(32))
    source_raw_transaction_id: Mapped[int | None] = mapped_column(
        BIGINT,
        ForeignKey("raw_transactions.id"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
