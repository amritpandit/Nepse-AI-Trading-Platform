"""Read-only technical-analysis endpoint for a stock's daily OHLCV history."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import sqlalchemy as sa
from app.db.session import get_db
from app.models.data_quality import DataTrust
from app.models.price import Price
from app.models.stock import Stock
from app.services.technical_analysis import analyze_ohlcv
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

router = APIRouter(prefix="/analysis", tags=["technical-analysis"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/technical/{symbol}")
def get_technical_analysis(
    symbol: str,
    db: DbSession,
    as_of: date | None = None,
    limit: Annotated[int, Query(ge=60, le=500)] = 250,
) -> dict:
    """Return causal indicators, SMC annotations and a transparent heuristic score.

    This GET endpoint is read-only. It deliberately reads an existing trust-score
    record rather than invoking DataQualityService, whose evaluation routine
    writes and commits DataTrust records.
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
        result = analyze_ohlcv(rows, normalized_symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    latest_date = observations[0].date
    trust = db.scalar(
        sa.select(DataTrust)
        .where(DataTrust.stock_id == stock.id, DataTrust.date == latest_date)
        .limit(1)
    )
    trust_score = float(trust.trust_score) if trust and trust.trust_score is not None else None
    quality_passed = trust_score is not None and trust_score >= 0.7

    result["data_quality"] = {
        "status": "verified" if quality_passed else "unverified_or_below_threshold",
        "trust_score": trust_score,
        "minimum_trust_score": 0.7,
        "passed": quality_passed,
        "note": (
            "Existing quality record passed the 0.70 trust threshold."
            if quality_passed
            else "A passing quality record for the latest bar is required before a BUY/SELL advisory is eligible."
        ),
    }

    if not quality_passed:
        result["signal"] = "HOLD"
        result["signal_eligible"] = False
        result["signal_gate_reason"] = "Latest-bar data quality is not verified at the required threshold."

    return result
