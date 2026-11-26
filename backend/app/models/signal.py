import datetime

from app.db.base import Base
from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Numeric, String, func, text
from sqlalchemy.orm import Mapped, mapped_column


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_stock_id_date", "stock_id", "date"),
        # One research-score event per stock, session and signal type. The
        # partial unique index prevents duplicate same-direction events while
        # allowing BUY and SELL events on the same stock/session.
        Index(
            "uq_signals_sector_stock_score_session",
            "stock_id",
            "date",
            "signal_type",
            "provider",
            "model_source",
            unique=True,
            postgresql_where=text(
                "provider = 'research_score' AND model_source = 'sector-stock-score-v1'"
            ),
            sqlite_where=text(
                "provider = 'research_score' AND model_source = 'sector-stock-score-v1'"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recorded_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    signal_type: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="rules")
    model_source: Mapped[str] = mapped_column(String(32), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=True)
    inference_cost: Mapped[float] = mapped_column(Numeric(10, 6), nullable=True)
    token_count: Mapped[int] = mapped_column(nullable=True)
    rule_params: Mapped[str] = mapped_column(String, nullable=True)
    target_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=True)
    take_profit: Mapped[float] = mapped_column(Numeric(18, 4), nullable=True)
    stop_loss: Mapped[float] = mapped_column(Numeric(18, 4), nullable=True)
    trailing_stop: Mapped[float] = mapped_column(Numeric(10, 6), nullable=True)
