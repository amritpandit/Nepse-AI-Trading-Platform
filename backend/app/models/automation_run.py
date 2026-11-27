"""Audit history for scheduled market-data and research automation runs."""
from __future__ import annotations

import datetime
from typing import Any

from app.db.base import Base
from sqlalchemy import DateTime, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column


class AutomationRun(Base):
    __tablename__ = "automation_runs"
    __table_args__ = (
        Index("ix_automation_runs_job_started", "job_type", "started_at"),
        Index("ix_automation_runs_run_key", "run_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    run_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    batch_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    records_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
