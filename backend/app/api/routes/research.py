"""Market-context, sector ranking and tradable-stock research routes."""
from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.core.security import get_current_user
from app.db.session import get_db
from app.models.price import Price
from app.models.signal import Signal
from app.models.stock import Stock
from app.models.user import User
from app.services.stock_sector_research import StockSectorResearchService
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

router = APIRouter(prefix="/research", tags=["research"])
DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


FORWARD_HORIZONS = (1, 5, 10, 20)


def _update_forward_outcomes(db: Session) -> dict[str, int]:
    """Refresh forward results for stored BUY/SELL triggers as new bars arrive.

    BUY is measured from the next session's open to the close after each horizon.
    SELL is evaluated as the return a long holder would have experienced by holding
    from the trigger close; it is not treated as a short-sale return.
    """
    signal_rows = db.scalars(
        sa.select(Signal).where(
            Signal.provider == "research_score",
            Signal.model_source == "sector-stock-score-v1",
            Signal.signal_type.in_(("BUY_TRIGGER", "SELL_TRIGGER")),
        )
    ).all()
    if not signal_rows:
        return {"signals_evaluated": 0, "horizon_results_completed": 0}

    signal_scope = (
        sa.select(
            Signal.id.label("signal_id"),
            Signal.stock_id.label("stock_id"),
            Signal.date.label("signal_date"),
        )
        .where(
            Signal.provider == "research_score",
            Signal.model_source == "sector-stock-score-v1",
            Signal.signal_type.in_(("BUY_TRIGGER", "SELL_TRIGGER")),
        )
        .subquery()
    )
    ranked_future = (
        sa.select(
            signal_scope.c.signal_id,
            Price.date.label("future_date"),
            Price.open.label("future_open"),
            Price.close.label("future_close"),
            sa.func.row_number()
            .over(
                partition_by=signal_scope.c.signal_id,
                order_by=Price.date,
            )
            .label("session_number"),
        )
        .join(
            Price,
            sa.and_(
                Price.stock_id == signal_scope.c.stock_id,
                Price.date > signal_scope.c.signal_date,
            ),
        )
        .subquery()
    )
    future_rows = db.execute(
        sa.select(
            ranked_future.c.signal_id,
            ranked_future.c.future_date,
            ranked_future.c.future_open,
            ranked_future.c.future_close,
            ranked_future.c.session_number,
        )
        .where(ranked_future.c.session_number <= max(FORWARD_HORIZONS))
        .order_by(ranked_future.c.signal_id, ranked_future.c.session_number)
    ).all()
    future_by_signal: dict[int, list[dict[str, Any]]] = {}
    for row in future_rows:
        future_by_signal.setdefault(int(row.signal_id), []).append(
            {
                "date": row.future_date.isoformat(),
                "open": float(row.future_open) if row.future_open is not None else None,
                "close": float(row.future_close) if row.future_close is not None else None,
            }
        )

    completed = 0
    for signal in signal_rows:
        try:
            details = json.loads(signal.rule_params or "{}")
        except (TypeError, ValueError):
            details = {}
        if not isinstance(details, dict):
            details = {}
        snapshot = details.get("snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        indicators = snapshot.get("indicators")
        indicators = indicators if isinstance(indicators, dict) else {}
        signal_close = snapshot.get("close")
        if not isinstance(signal_close, int | float):
            signal_close = indicators.get("close")
        try:
            signal_close_value = float(signal_close) if signal_close is not None else None
        except (TypeError, ValueError):
            signal_close_value = None

        future = future_by_signal.get(signal.id, [])
        outcomes: dict[str, dict[str, Any]] = {}
        for horizon in FORWARD_HORIZONS:
            key = str(horizon)
            if len(future) < horizon:
                outcomes[key] = {
                    "status": "pending",
                    "available_future_sessions": len(future),
                    "required_future_sessions": horizon,
                }
                continue

            exit_bar = future[horizon - 1]
            exit_close = exit_bar.get("close")
            if signal.signal_type == "BUY_TRIGGER":
                entry_open = future[0].get("open")
                if (
                    not isinstance(entry_open, int | float)
                    or entry_open <= 0
                    or not isinstance(exit_close, int | float)
                    or exit_close <= 0
                ):
                    outcomes[key] = {"status": "unavailable", "reason": "Missing valid OHLC prices"}
                    continue
                return_pct = (float(exit_close) / float(entry_open) - 1.0) * 100.0
                outcomes[key] = {
                    "status": "complete",
                    "entry_basis": "next_session_open",
                    "entry_date": future[0]["date"],
                    "entry_price": float(entry_open),
                    "exit_date": exit_bar["date"],
                    "exit_price": float(exit_close),
                    "gross_return_pct": round(return_pct, 4),
                    "successful": return_pct > 0,
                }
            else:
                if (
                    signal_close_value is None
                    or signal_close_value <= 0
                    or not isinstance(exit_close, int | float)
                    or exit_close <= 0
                ):
                    outcomes[key] = {"status": "unavailable", "reason": "Missing valid trigger/forward close"}
                    continue
                holding_return_pct = (float(exit_close) / signal_close_value - 1.0) * 100.0
                outcomes[key] = {
                    "status": "complete",
                    "entry_basis": "trigger_session_close_for_hold_comparison",
                    "signal_close": signal_close_value,
                    "exit_date": exit_bar["date"],
                    "exit_price": float(exit_close),
                    "holding_return_pct": round(holding_return_pct, 4),
                    "successful_exit": holding_return_pct < 0,
                }

        performance = {
            "method": (
                "BUY: next trading session open to horizon close; SELL: subsequent long-holding "
                "return from trigger close. Daily OHLC observations; gross returns before fees, "
                "slippage, taxes or liquidity effects."
            ),
            "horizons_trading_sessions": list(FORWARD_HORIZONS),
            "latest_forward_session": future[-1]["date"] if future else None,
            "outcomes": outcomes,
        }
        if details.get("forward_performance") != performance:
            details["forward_performance"] = performance
            signal.rule_params = json.dumps(details, default=str)
            completed += sum(1 for value in outcomes.values() if value.get("status") == "complete")

    return {
        "signals_evaluated": len(signal_rows),
        "horizon_results_completed": completed,
    }


@router.get("/market-regime")
def market_regime(db: DbSession) -> dict:
    """NEPSE Index is context/benchmark only; it is never a stock signal."""
    service = StockSectorResearchService(db)
    return service.sector_ranking()["market_context"]


@router.get("/sectors")
def sector_ranking(db: DbSession) -> dict:
    """Rank sectors by member-stock median returns, breadth and NEPSE relative strength."""
    return StockSectorResearchService(db).sector_ranking()


@router.post("/signals/refresh")
def refresh_and_save_stock_signals(
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=300)] = 300,
) -> dict:
    """Run a current stock scan and idempotently save one research snapshot per stock/session."""
    scan = StockSectorResearchService(db).scan_stocks(limit=limit)
    signal_rows = scan.get("signals", [])
    symbols = [str(item.get("symbol", "")).upper() for item in signal_rows]
    stocks = db.scalars(sa.select(Stock).where(Stock.symbol.in_(symbols))).all() if symbols else []
    stock_by_symbol = {item.symbol.upper(): item for item in stocks}

    saved = 0
    counts: dict[str, int] = {}
    for item in signal_rows:
        stock = stock_by_symbol.get(str(item.get("symbol", "")).upper())
        if stock is None:
            continue
        as_of = date.fromisoformat(str(item["as_of"]))
        signal_type = str(item.get("signal", "HOLD"))
        buy_score = int(item.get("buy_score", 0))
        sell_score = int(item.get("sell_score", 0))
        score_margin = int(item.get("score_margin", 0))
        indicators = item.get("indicators")
        indicators = indicators if isinstance(indicators, dict) else {}
        rule_params = {
            "model_version": "sector-stock-score-v1",
            "buy_score": buy_score,
            "sell_score": sell_score,
            "score_margin": score_margin,
            "score_type": "evidence points, not a probability",
            "score_model": item.get("score_model"),
            "market_context": item.get("market_context"),
            "sector_context": item.get("sector_context"),
            "data_freshness": item.get("data_freshness"),
            "data_quality": item.get("data_quality"),
            "signal_reason": item.get("signal_reason"),
            "snapshot": {
                "close": indicators.get("close"),
                "indicators": indicators,
                "confirmation_gates": item.get("confirmation_gates"),
                "confirmation_checks": item.get("confirmation_checks"),
                "smart_money_concepts": item.get("smart_money_concepts"),
                "price_action": item.get("price_action"),
                "reference_risk_reward_ratio": item.get("reference_risk_reward_ratio"),
            },
        }
        values = {
            "signal_type": signal_type,
            "value": float(score_margin) / 100.0,
            "confidence": None,
            "provider": "research_score",
            "model_source": "sector-stock-score-v1",
            "rule_params": json.dumps(rule_params, default=str),
            "target_price": item.get("reference_target_level"),
            "take_profit": item.get("reference_target_level"),
            "stop_loss": item.get("reference_stop_level"),
            "updated_at": sa.func.now(),
        }
        key_values = {
            "stock_id": stock.id,
            "date": as_of,
            **values,
        }
        dialect_name = db.get_bind().dialect.name
        if dialect_name == "postgresql":
            statement = pg_insert(Signal).values(**key_values)
            statement = statement.on_conflict_do_update(
                index_elements=["stock_id", "date", "signal_type", "provider", "model_source"],
                index_where=sa.text(
                    "provider = 'research_score' AND model_source = 'sector-stock-score-v1'"
                ),
                set_=values,
            )
            db.execute(statement)
        elif dialect_name == "sqlite":
            statement = sqlite_insert(Signal).values(**key_values)
            statement = statement.on_conflict_do_update(
                index_elements=["stock_id", "date", "signal_type", "provider", "model_source"],
                index_where=sa.text(
                    "provider = 'research_score' AND model_source = 'sector-stock-score-v1'"
                ),
                set_=values,
            )
            db.execute(statement)
        else:
            existing = db.scalar(
                sa.select(Signal).where(
                    Signal.stock_id == stock.id,
                    Signal.date == as_of,
                    Signal.provider == "research_score",
                    Signal.model_source == "sector-stock-score-v1",
                )
            )
            if existing is None:
                db.add(Signal(**key_values))
            else:
                for key, value in values.items():
                    setattr(existing, key, value)
        saved += 1
        counts[signal_type] = counts.get(signal_type, 0) + 1

    db.flush()
    outcome_refresh = _update_forward_outcomes(db)
    db.commit()
    buy_count = counts.get("BUY_TRIGGER", 0)
    sell_count = counts.get("SELL_TRIGGER", 0)
    display_signals = [
        {key: value for key, value in item.items() if key != "price_history_for_chart"}
        for item in signal_rows
    ]
    return {
        "as_of": scan.get("as_of"),
        "market_context": scan.get("market_context"),
        "stocks_analyzed": scan.get("stocks_analyzed", 0),
        "snapshots_saved": saved,
        "signal_counts": counts,
        "buy_triggers_recorded": buy_count,
        "sell_triggers_recorded": sell_count,
        "trigger_records_updated": buy_count + sell_count,
        "outcome_refresh": outcome_refresh,
        "signals": display_signals,
        "provider": "research_score",
        "model_version": "sector-stock-score-v1",
        "value_semantics": "signed score margin divided by 100; not a probability",
    }


@router.get("/signals/history")
def get_saved_stock_signal_history(
    db: DbSession,
    symbol: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 300,
) -> dict:
    """Retrieve persisted score-model signal snapshots without running a new scan."""
    query = (
        sa.select(Signal, Stock)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(
            Signal.provider == "research_score",
            Signal.model_source == "sector-stock-score-v1",
        )
        .order_by(Signal.date.desc(), Stock.symbol)
        .limit(limit)
    )
    if symbol:
        query = query.where(Stock.symbol == symbol.strip().upper())
    rows = db.execute(query).all()
    history = []
    for signal, stock in rows:
        try:
            score_details = json.loads(signal.rule_params or "{}")
        except (TypeError, ValueError):
            score_details = {}
        history.append(
            {
                "date": signal.date.isoformat(),
                "recorded_at": signal.recorded_at.isoformat() if signal.recorded_at else None,
                "updated_at": signal.updated_at.isoformat() if signal.updated_at else None,
                "symbol": stock.symbol,
                "name": stock.name,
                "sector": stock.sector,
                "signal": signal.signal_type,
                "score_margin": round(float(signal.value) * 100, 2),
                "buy_score": score_details.get("buy_score"),
                "sell_score": score_details.get("sell_score"),
                "signal_reason": score_details.get("signal_reason"),
                "price_at_signal": (score_details.get("snapshot") or {}).get("close"),
                "data_freshness": score_details.get("data_freshness"),
                "forward_performance": score_details.get("forward_performance", {}),
                "stop_reference": float(signal.stop_loss) if signal.stop_loss is not None else None,
                "target_reference": float(signal.target_price) if signal.target_price is not None else None,
                "record_id": signal.id,
            }
        )
    return {"count": len(history), "signals": history}


@router.get("/signals/performance")
def get_saved_stock_signal_performance(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=10000)] = 5000,
) -> dict:
    """Aggregate matured forward tests for recorded BUY and SELL trigger snapshots."""
    rows = db.scalars(
        sa.select(Signal)
        .where(
            Signal.provider == "research_score",
            Signal.model_source == "sector-stock-score-v1",
            Signal.signal_type.in_(("BUY_TRIGGER", "SELL_TRIGGER")),
        )
        .order_by(Signal.date.desc(), Signal.id.desc())
        .limit(limit)
    ).all()
    values: dict[str, dict[int, list[float]]] = {
        "BUY_TRIGGER": {horizon: [] for horizon in FORWARD_HORIZONS},
        "SELL_TRIGGER": {horizon: [] for horizon in FORWARD_HORIZONS},
    }
    success: dict[str, dict[int, int]] = {
        "BUY_TRIGGER": {horizon: 0 for horizon in FORWARD_HORIZONS},
        "SELL_TRIGGER": {horizon: 0 for horizon in FORWARD_HORIZONS},
    }
    for signal in rows:
        try:
            details = json.loads(signal.rule_params or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(details, dict):
            continue
        performance = details.get("forward_performance")
        performance = performance if isinstance(performance, dict) else {}
        outcomes = performance.get("outcomes")
        outcomes = outcomes if isinstance(outcomes, dict) else {}
        for horizon in FORWARD_HORIZONS:
            outcome = outcomes.get(str(horizon))
            if not isinstance(outcome, dict) or outcome.get("status") != "complete":
                continue
            if signal.signal_type == "BUY_TRIGGER":
                result = outcome.get("gross_return_pct")
                was_successful = outcome.get("successful") is True
            else:
                result = outcome.get("holding_return_pct")
                was_successful = outcome.get("successful_exit") is True
            if isinstance(result, int | float):
                values[signal.signal_type][horizon].append(float(result))
                if was_successful:
                    success[signal.signal_type][horizon] += 1

    horizons: dict[str, dict[str, dict[str, Any]]] = {}
    for horizon in FORWARD_HORIZONS:
        by_direction: dict[str, dict[str, Any]] = {}
        for signal_type, key in (("BUY_TRIGGER", "buy"), ("SELL_TRIGGER", "sell")):
            returns = values[signal_type][horizon]
            sample_count = len(returns)
            average = sum(returns) / sample_count if sample_count else None
            success_rate = (
                success[signal_type][horizon] / sample_count * 100.0
                if sample_count
                else None
            )
            if signal_type == "BUY_TRIGGER":
                by_direction[key] = {
                    "samples": sample_count,
                    "average_gross_return_pct": round(average, 4) if average is not None else None,
                    "positive_return_rate_pct": round(success_rate, 2) if success_rate is not None else None,
                }
            else:
                by_direction[key] = {
                    "samples": sample_count,
                    "average_subsequent_holding_return_pct": round(average, 4) if average is not None else None,
                    "favorable_exit_rate_pct": round(success_rate, 2) if success_rate is not None else None,
                }
        horizons[str(horizon)] = by_direction

    return {
        "model_version": "sector-stock-score-v1",
        "records_included": len(rows),
        "buy_trigger_count": sum(1 for row in rows if row.signal_type == "BUY_TRIGGER"),
        "sell_trigger_count": sum(1 for row in rows if row.signal_type == "SELL_TRIGGER"),
        "horizons": horizons,
        "interpretation": {
            "buy": "Gross hypothetical long return from next-session open; excludes costs and liquidity effects.",
            "sell": "Subsequent return from trigger close for an existing long holder; a negative result is a favorable exit review, not a short return.",
            "not_a_probability": True,
        },
    }


@router.get("/stocks/{symbol}")
def stock_research(symbol: str, db: DbSession) -> dict:
    """Detailed technical trigger, market/sector confirmation and historical event study."""
    try:
        return StockSectorResearchService(db).analyze_stock(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/signals")
def scan_stock_signals(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=300)] = 100,
) -> dict:
    """Scan eligible ordinary shares; only individual stocks can have BUY/SELL triggers."""
    return StockSectorResearchService(db).scan_stocks(limit=limit)
