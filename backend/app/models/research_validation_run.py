"""Persistent audit records for universe-level score validation runs."""
from __future__ import annotations

import datetime
from typing import Any

from app.db.base import Base
from app.models.types import json_dict_type
from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column


class ResearchValidationRun(Base):
    __tablename__ = "research_validation_runs"
    __table_args__ = (
        Index("ix_research_validation_runs_user_started", "user_id", "started_at"),
        Index("ix_research_validation_runs_model_started", "model_version", "started_at"),
        Index("ix_research_validation_runs_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_version: Mapped[str] = mapped_column(String(80), nullable=False)
    sector_filter: Mapped[str | None] = mapped_column(String(120), nullable=True)
    requested_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_samples: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    symbols_analyzed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rank_eligible_symbols: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latest_price_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(json_dict_type, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(json_dict_type, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
