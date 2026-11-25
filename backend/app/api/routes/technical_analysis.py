"""Read-only technical-analysis endpoint for a stock's daily OHLCV history."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

import numpy as np
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.benchmark import NEPSEIndex
from app.models.price import Price
from app.models.stock import Stock
from app.services.stock_sector_research import StockSectorResearchService
from app.services.technical_analysis import analyze_ohlcv

router = APIRouter(prefix="/analysis", tags=["technical-analysis"])
DbSession = Annotated[Session, Depends(get_db)]


def _json_safe(value: Any) -> Any:
    """Convert NumPy scalar values nested in research output to JSON-native types."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return value


@router.get("/technical/{symbol}")
def get_technical_analysis(
    symbol: str,
    db: DbSession,
    as_of: date | None = None,
    limit: Annotated[int, Query(ge=60, le=500)] = 250,
) -> dict:
    """Return causal indicators, SMC events and the canonical composite research score.

    This GET endpoint is read-only. The directional score is delegated to the
    existing sector-stock-score-v1 model; this module adds per-bar indicator
    and SMC history rather than generating a second trading decision.
    """
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise HTTPException(status_code=422, detail="A stock symbol is required.")

    stock = db.scalar(sa.select(Stock).where(Stock.symbol == normalized_symbol))
    if stock is None:
        raise HTTPException(status_code=404, detail=f"Stock {normalized_symbol} was not found.")

    query = sa.select(
        Price.date,
        Price.open,
        Price.high,
        Price.low,
        Price.close,
        Price.volume,
    ).where(Price.stock_id == stock.id)
    if as_of is not None:
        query = query.where(Price.date <= as_of)
    query = query.order_by(Price.date.desc()).limit(limit)

    observations = db.execute(query).all()
    if not observations:
        raise HTTPException(
            status_code=404,
            detail=f"No historical OHLCV data is available for {normalized_symbol}.",
        )

    rows = [
        {
            "date": row.date,
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
        }
        for row in reversed(observations)
    ]

    try:
        result = analyze_ohlcv(rows, normalized_symbol, display_limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    database_latest_date = db.scalar(
        sa.select(sa.func.max(Price.date)).where(Price.stock_id == stock.id)
    )
    database_latest_benchmark_date = db.scalar(
        sa.select(sa.func.max(NEPSEIndex.date))
    )
    composite_research: dict[str, Any] | None = None
    research_status = "available"

    # The canonical score model is intentionally current-session only. When an
    # older as_of date is requested, do not attach a score calculated from
    # newer observations. Also suppress it whenever the latest stock and
    # NEPSE Index dates differ: the existing scorer reads the latest index bar.
    if as_of is not None and database_latest_date is not None and as_of < database_latest_date:
        research_status = "historical_composite_snapshot_not_available"
    elif (
        database_latest_date is not None
        and database_latest_benchmark_date is not None
        and database_latest_benchmark_date > database_latest_date
    ):
        research_status = "benchmark_history_ahead_of_stock_history"
    elif (
        database_latest_date is not None
        and database_latest_benchmark_date is not None
        and database_latest_benchmark_date < database_latest_date
    ):
        # A benchmark that trails the latest stock bar is not a same-session
        # comparison. Do not produce a fresh BUY/SELL score from mismatched dates.
        research_status = "benchmark_history_behind_stock_history"
    else:
        try:
            research = StockSectorResearchService(db).analyze_stock(
                normalized_symbol,
                include_historical_validation=False,
            )
            composite_research = {
                key: research.get(key)
                for key in [
                    "as_of",
                    "signal",
                    "signal_reason",
                    "confidence_score",
                    "buy_score",
                    "sell_score",
                    "score_margin",
                    "score_model",
                    "confirmation_gates",
                    "confirmation_checks",
                    "smart_money_concepts",
                    "market_context",
                    "sector_context",
                    "indicators",
                    "price_action",
                    "reference_stop_level",
                    "reference_target_level",
                    "reference_risk_reward_ratio",
                    "data_freshness",
                    "data_quality",
                    "execution_note",
                    "price_history_for_chart",
                ]
            }
        except ValueError as exc:
            research_status = "unavailable"
            composite_research = {
                "signal": "HOLD",
                "signal_reason": str(exc),
                "buy_score": 0,
                "sell_score": 0,
                "score_margin": 0,
            }

    result["composite_research_model"] = composite_research
    result["composite_research_status"] = research_status
    result["composite_research_version"] = "sector-stock-score-v1"
    result["research_model_note"] = (
        "BUY_TRIGGER/SELL_TRIGGER/HOLD comes from the platform's sector-stock-score-v1 "
        "model, including NEPSE regime, sector relative strength, trend, momentum, "
        "volume, price action, SMC and data-freshness gates. The technical-analysis "
        "module adds per-bar indicator and SMC history; it is not a separate trading model."
    )

    return _json_safe(result)
