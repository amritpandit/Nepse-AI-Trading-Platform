"""Market-context, sector ranking and tradable-stock research routes."""
from __future__ import annotations

import hmac
import json

import jwt
from datetime import UTC, date, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError, PyJWTError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.core.config import settings
from app.core.security import get_current_user
from app.db.session import get_db
from app.models.automation_run import AutomationRun
from app.models.benchmark import NEPSEIndex
from app.models.price import Price
from app.models.signal import Signal
from app.models.stock import Stock
from app.models.user import User
from app.services.nepal_market_data import NepalMarketDataImporter
from app.services.stock_sector_research import DISCONTINUITY_THRESHOLD, StockSectorResearchService
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

router = APIRouter(prefix="/research", tags=["research"])

GITHUB_ACTIONS_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_ACTIONS_OIDC_AUDIENCE = "nepse-ai-automation"
GITHUB_ACTIONS_REPOSITORY = "amritpandit/Nepse-AI-Trading-Platform"
GITHUB_ACTIONS_REPOSITORY_ID = "1469481611"
GITHUB_ACTIONS_OWNER_ID = "357862676"
GITHUB_ACTIONS_WORKFLOW_REF = (
    "amritpandit/Nepse-AI-Trading-Platform/"
    ".github/workflows/nepse-automated-scan.yml@refs/heads/main"
)
_github_actions_jwks = PyJWKClient(
    f"{GITHUB_ACTIONS_OIDC_ISSUER}/.well-known/jwks",
    cache_keys=True,
)
DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


FORWARD_HORIZONS = (1, 5, 10, 20)


def _update_forward_outcomes(db: Session) -> dict[str, int]:
    """Refresh forward results for stored BUY/SELL triggers as new bars arrive.

    BUY is measured from the next session's open to the close after each horizon.
    SELL assumes an existing holder could exit at the next session's open, then
    measures the price move they would have experienced by continuing to hold.
    """
    signal_rows = db.scalars(
        sa.select(Signal).where(
            Signal.provider == "research_score",
            Signal.model_source == "sector-stock-score-v1",
            Signal.signal_type.in_(("BUY_TRIGGER", "SELL_TRIGGER")),
        )
    ).all()
    if not signal_rows:
        return {
            "signals_evaluated": 0,
            "signals_reprocessed": 0,
            "horizon_results_completed": 0,
        }

    details_by_signal: dict[int, dict[str, Any]] = {}
    signals_to_update: list[Signal] = []
    for signal in signal_rows:
        try:
            parsed = json.loads(signal.rule_params or "{}")
        except (TypeError, ValueError):
            parsed = {}
        details = parsed if isinstance(parsed, dict) else {}
        details_by_signal[signal.id] = details
        performance = details.get("forward_performance")
        performance = performance if isinstance(performance, dict) else {}
        existing_outcomes = performance.get("outcomes")
        existing_outcomes = (
            existing_outcomes if isinstance(existing_outcomes, dict) else {}
        )
        all_horizons_final = all(
            isinstance(existing_outcomes.get(str(horizon)), dict)
            and existing_outcomes[str(horizon)].get("status") in {"complete", "review_required"}
            for horizon in FORWARD_HORIZONS
        )
        if not all_horizons_final:
            signals_to_update.append(signal)

    if not signals_to_update:
        return {
            "signals_evaluated": len(signal_rows),
            "signals_reprocessed": 0,
            "horizon_results_completed": 0,
        }

    signal_ids = [signal.id for signal in signals_to_update]
    signal_scope = (
        sa.select(
            Signal.id.label("signal_id"),
            Signal.stock_id.label("stock_id"),
            Signal.date.label("signal_date"),
        )
        .where(Signal.id.in_(signal_ids))
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
    for signal in signals_to_update:
        details = details_by_signal.get(signal.id, {})
        future = future_by_signal.get(signal.id, [])
        snapshot = details.get("snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        previous_close = snapshot.get("close")
        flagged_gaps: list[dict[str, Any]] = []
        for bar in future:
            current_close = bar.get("close")
            if (
                isinstance(previous_close, int | float)
                and previous_close > 0
                and isinstance(current_close, int | float)
                and current_close > 0
            ):
                change_pct = (float(current_close) / float(previous_close) - 1.0) * 100.0
                if abs(change_pct) > DISCONTINUITY_THRESHOLD * 100.0:
                    flagged_gaps.append(
                        {
                            "date": bar["date"],
                            "previous_close": round(float(previous_close), 4),
                            "close": round(float(current_close), 4),
                            "change_pct": round(change_pct, 4),
                        }
                    )
            if isinstance(current_close, int | float) and current_close > 0:
                previous_close = current_close

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
            horizon_gaps = [
                gap for gap in flagged_gaps if gap["date"] <= exit_bar["date"]
            ]
            if horizon_gaps:
                outcomes[key] = {
                    "status": "review_required",
                    "reason": (
                        "Raw close-to-close price discontinuity exceeded the "
                        "research review threshold; verify corporate action or source data."
                    ),
                    "flagged_price_gaps": horizon_gaps,
                }
                continue

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
                exit_open = future[0].get("open")
                if (
                    not isinstance(exit_open, int | float)
                    or exit_open <= 0
                    or not isinstance(exit_close, int | float)
                    or exit_close <= 0
                ):
                    outcomes[key] = {"status": "unavailable", "reason": "Missing valid next-session open or forward close"}
                    continue
                holding_return_pct = (float(exit_close) / float(exit_open) - 1.0) * 100.0
                outcomes[key] = {
                    "status": "complete",
                    "entry_basis": "next_session_open_assumed_exit",
                    "assumed_exit_date": future[0]["date"],
                    "assumed_exit_price": float(exit_open),
                    "comparison_date": exit_bar["date"],
                    "comparison_price": float(exit_close),
                    "holding_return_pct": round(holding_return_pct, 4),
                    "successful_exit": holding_return_pct < 0,
                }

        performance = {
            "method": (
                "BUY: next-session open to horizon close; SELL: next-session-open exit assumption "
                "compared with continued holding to horizon close. Daily OHLC observations; "
                "gross returns before fees, "
                "slippage, taxes or liquidity effects."
            ),
            "horizons_trading_sessions": list(FORWARD_HORIZONS),
            "latest_forward_session": future[-1]["date"] if future else None,
            "corporate_action_adjusted": False,
            "price_gap_review_threshold_pct": round(DISCONTINUITY_THRESHOLD * 100.0, 2),
            "flagged_price_gaps": flagged_gaps,
            "outcomes": outcomes,
        }
        if details.get("forward_performance") != performance:
            details["forward_performance"] = performance
            signal.rule_params = json.dumps(details, default=str)
            completed += sum(1 for value in outcomes.values() if value.get("status") == "complete")

    return {
        "signals_evaluated": len(signal_rows),
        "signals_reprocessed": len(signals_to_update),
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
    """Run a full-universe stock scan and idempotently save one snapshot per stock/session."""
    eligible_universe_size = db.scalar(
        sa.select(sa.func.count())
        .select_from(Stock)
        .where(
            Stock.instrument_type == "ordinary",
            Stock.is_active.is_(True),
        )
    ) or 0
    scan_limit = max(limit, int(eligible_universe_size))
    scan = StockSectorResearchService(db).scan_stocks(limit=scan_limit)
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
            pg_statement = pg_insert(Signal).values(**key_values)
            pg_statement = pg_statement.on_conflict_do_update(
                index_elements=["stock_id", "date", "signal_type", "provider", "model_source"],
                index_where=sa.text(
                    "provider = 'research_score' AND model_source = 'sector-stock-score-v1'"
                ),
                set_=values,
            )
            db.execute(pg_statement)
        elif dialect_name == "sqlite":
            sqlite_statement = sqlite_insert(Signal).values(**key_values)
            sqlite_statement = sqlite_statement.on_conflict_do_update(
                index_elements=["stock_id", "date", "signal_type", "provider", "model_source"],
                index_where=sa.text(
                    "provider = 'research_score' AND model_source = 'sector-stock-score-v1'"
                ),
                set_=values,
            )
            db.execute(sqlite_statement)
        else:
            existing = db.scalar(
                sa.select(Signal).where(
                    Signal.stock_id == stock.id,
                    Signal.date == as_of,
                    Signal.signal_type == signal_type,
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
    # PostgreSQL/SQLite upserts execute as Core DML and do not necessarily
    # synchronize already-loaded ORM Signal instances. Expire the identity map
    # before reading the ledger so forward-outcome refresh always sees the
    # just-upserted scores and snapshot payloads, including repeated scans in
    # a reused Session.
    db.expire_all()
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
    triggers_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=10000)] = 1000,
) -> dict:
    """Retrieve persisted score-model signal snapshots without running a new scan."""
    query = (
        sa.select(Signal, Stock)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(
            Signal.provider == "research_score",
            Signal.model_source == "sector-stock-score-v1",
        )
        .order_by(Signal.date.desc(), Signal.recorded_at.desc(), Stock.symbol, Signal.signal_type)
        .limit(limit)
    )
    if symbol:
        query = query.where(Stock.symbol == symbol.strip().upper())
    if triggers_only:
        query = query.where(Signal.signal_type.in_(("BUY_TRIGGER", "SELL_TRIGGER")))
    rows = db.execute(query).all()
    history = []
    for signal, stock in rows:
        try:
            score_details = json.loads(signal.rule_params or "{}")
        except (TypeError, ValueError):
            score_details = {}
        snapshot = score_details.get("snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
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
                "score_model": score_details.get("score_model"),
                "signal_reason": score_details.get("signal_reason"),
                "market_context": score_details.get("market_context"),
                "sector_context": score_details.get("sector_context"),
                "data_quality": score_details.get("data_quality"),
                "snapshot": snapshot,
                "price_at_signal": snapshot.get("close"),
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
    review_required: dict[str, dict[int, int]] = {
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
            if not isinstance(outcome, dict):
                continue
            if outcome.get("status") == "review_required":
                review_required[signal.signal_type][horizon] += 1
                continue
            if outcome.get("status") != "complete":
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
                    "review_required_samples": review_required[signal_type][horizon],
                }
            else:
                by_direction[key] = {
                    "samples": sample_count,
                    "average_subsequent_holding_return_pct": round(average, 4) if average is not None else None,
                    "favorable_exit_rate_pct": round(success_rate, 2) if success_rate is not None else None,
                    "review_required_samples": review_required[signal_type][horizon],
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
            "sell": "Return from next-session open exit reference to horizon close for an existing long holder; a negative result is a favorable exit review, not a short return.",
            "corporate_action_adjusted": False,
            "data_warning": (
                "Stored NEPSE OHLC prices are raw and not corporate-action adjusted. "
                "Horizons containing a close-to-close discontinuity above the 15% "
                "review threshold are excluded until corporate actions or source data are verified."
            ),
            "gross_of_costs": True,
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
    include_charts: bool = False,
) -> dict:
    """Scan eligible ordinary shares; omit bulky chart history unless explicitly requested."""
    result = StockSectorResearchService(db).scan_stocks(limit=limit)
    if not include_charts:
        for item in result.get("signals", []):
            item.pop("price_history_for_chart", None)
    return result



def _start_automation_run(
    db: Session,
    *,
    job_type: str,
    run_key: str | None = None,
    batch_offset: int | None = None,
) -> int:
    """Commit a running audit row before potentially long import or scan work."""
    row = AutomationRun(
        job_type=job_type,
        status="running",
        run_key=run_key,
        batch_offset=batch_offset,
        started_at=datetime.now(UTC),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def _finish_automation_run(
    db: Session,
    run_id: int,
    *,
    status: str,
    records_processed: int = 0,
    records_inserted: int = 0,
    records_updated: int = 0,
    summary: dict[str, Any] | None = None,
    error_category: str | None = None,
) -> AutomationRun:
    """Finalize a previously committed running row without exposing credentials."""
    row = db.get(AutomationRun, run_id)
    if row is None:
        raise ValueError(f"Automation run {run_id} was not found")
    row.status = status
    row.completed_at = datetime.now(UTC)
    row.records_processed = records_processed
    row.records_inserted = records_inserted
    row.records_updated = records_updated
    row.summary = summary
    row.error_category = error_category
    db.commit()
    db.refresh(row)
    return row


def _verify_github_actions_oidc_token(token: str) -> bool:
    """Accept only short-lived OIDC tokens from this repository's scheduled scan workflow."""
    try:
        signing_key = _github_actions_jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=GITHUB_ACTIONS_OIDC_AUDIENCE,
            issuer=GITHUB_ACTIONS_OIDC_ISSUER,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except (PyJWKClientError, PyJWTError, OSError, TimeoutError):
        return False

    return (
        claims.get("repository") == GITHUB_ACTIONS_REPOSITORY
        and str(claims.get("repository_id")) == GITHUB_ACTIONS_REPOSITORY_ID
        and str(claims.get("repository_owner_id")) == GITHUB_ACTIONS_OWNER_ID
        and claims.get("workflow_ref") == GITHUB_ACTIONS_WORKFLOW_REF
        and claims.get("ref") == "refs/heads/main"
        and claims.get("event_name") in {"schedule", "workflow_dispatch"}
    )


def _require_signal_automation_token(
    token: Annotated[str | None, Header(alias="X-Automation-Token")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> None:
    """Authenticate the private static key or this repository's GitHub Actions OIDC identity."""
    if authorization and authorization.startswith("Bearer "):
        oidc_token = authorization.removeprefix("Bearer ").strip()
        if oidc_token and _verify_github_actions_oidc_token(oidc_token):
            return

    expected = settings.signal_automation_token
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Signal automation is not configured on this API service.",
        )
    if token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid automation credentials.")


@router.post("/automation/import-batch")
def automation_import_market_data(
    db: DbSession,
    _authorized: Annotated[None, Depends(_require_signal_automation_token)],
    offset: Annotated[int, Query(ge=0)] = 0,
    batch_size: Annotated[int, Query(ge=1, le=10)] = 10,
    start_date: str | None = None,
    end_date: str | None = None,
    run_id: Annotated[str | None, Header(alias="X-Automation-Run-ID")] = None,
) -> dict[str, Any]:
    """Import one bounded batch and persist its ingestion result in the audit history."""
    run_key = run_id.strip()[:120] if run_id else None
    audit_run_id = _start_automation_run(
        db,
        job_type="market_data_import",
        run_key=run_key,
        batch_offset=offset,
    )
    importer = NepalMarketDataImporter(session=db)
    try:
        result = importer.sync_equities(
            offset=offset,
            batch_size=batch_size,
            start_date=start_date,
            end_date=end_date,
        )
        index_result = None
        if offset == 0:
            index_result = importer.sync_nepse_index(
                start_date=start_date,
                end_date=end_date,
            )
            result["index_sync"] = index_result

        requested = int(result.get("symbols_requested", 0))
        succeeded = int(result.get("symbols_succeeded", 0))
        errors = result.get("errors", [])
        complete = not errors and requested == succeeded
        audit = _finish_automation_run(
            db,
            audit_run_id,
            status="success" if complete else "failed",
            records_processed=int(result.get("rows_fetched", 0)),
            records_inserted=int(result.get("rows_inserted", 0)),
            records_updated=int(result.get("rows_updated", 0)),
            summary={
                "batch_size": batch_size,
                "symbols_requested": requested,
                "symbols_succeeded": succeeded,
                "rows_rejected": int(result.get("rows_rejected", 0)),
                "start_date": start_date,
                "end_date": end_date,
                "index_records_inserted": (
                    int(index_result.get("inserted", 0))
                    if isinstance(index_result, dict)
                    else 0
                ),
                "error_count": len(errors) if isinstance(errors, list) else 0,
            },
            error_category="MarketDataImportIncomplete" if not complete else None,
        )
        result["automation_run_id"] = audit.id
        if not complete:
            raise HTTPException(
                status_code=502,
                detail="Market-data batch incomplete; inspect automation run history.",
            )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        _finish_automation_run(
            db,
            audit_run_id,
            status="failed",
            summary={"batch_size": batch_size, "start_date": start_date, "end_date": end_date},
            error_category=type(exc).__name__,
        )
        raise
    finally:
        importer.close()


@router.post("/automation/scan")
def automation_run_market_scan(
    db: DbSession,
    _authorized: Annotated[None, Depends(_require_signal_automation_token)],
    run_id: Annotated[str | None, Header(alias="X-Automation-Run-ID")] = None,
) -> dict[str, Any]:
    """Run the full-universe scan and persist its outcome in the automation audit history."""
    run_key = run_id.strip()[:120] if run_id else None
    audit_run_id = _start_automation_run(
        db,
        job_type="signal_scan",
        run_key=run_key,
    )
    try:
        result = refresh_and_save_stock_signals(user=None, db=db, limit=300)  # type: ignore[arg-type]
        counts = result.get("signal_counts", {})
        counts = counts if isinstance(counts, dict) else {}
        audit = _finish_automation_run(
            db,
            audit_run_id,
            status="success" if int(result.get("stocks_analyzed", 0)) > 0 else "failed",
            records_processed=int(result.get("stocks_analyzed", 0)),
            summary={
                "as_of": result.get("as_of"),
                "stocks_analyzed": result.get("stocks_analyzed", 0),
                "snapshots_saved": result.get("snapshots_saved", 0),
                "signal_counts": counts,
                "outcome_refresh": result.get("outcome_refresh", {}),
                "model_version": result.get("model_version"),
            },
            error_category=(
                "EmptyScan" if int(result.get("stocks_analyzed", 0)) <= 0 else None
            ),
        )
        result["automation_run_id"] = audit.id
        return result
    except Exception as exc:
        db.rollback()
        _finish_automation_run(
            db,
            audit_run_id,
            status="failed",
            error_category=type(exc).__name__,
        )
        raise


@router.get("/automation/overview")
def get_automation_overview(db: DbSession) -> dict[str, Any]:
    """Expose read-only data coverage, saved signals, forward tests and pipeline health."""
    as_of = datetime.now(ZoneInfo("Asia/Kathmandu")).date()

    active_ordinary_count = db.scalar(
        sa.select(sa.func.count(Stock.id)).where(
            Stock.is_active.is_(True),
            Stock.instrument_type == "ordinary",
        )
    ) or 0
    price_stats = db.execute(
        sa.select(
            sa.func.count(Price.id),
            sa.func.count(sa.distinct(Price.stock_id)),
            sa.func.min(Price.date),
            sa.func.max(Price.date),
        )
        .join(Stock, Stock.id == Price.stock_id)
        .where(
            Stock.is_active.is_(True),
            Stock.instrument_type == "ordinary",
        )
    ).one()
    price_rows = int(price_stats[0] or 0)
    covered_ordinary_count = int(price_stats[1] or 0)
    first_price_date = price_stats[2]
    latest_price_date = price_stats[3]

    latest_price_by_stock = (
        sa.select(
            Price.stock_id.label("stock_id"),
            sa.func.max(Price.date).label("latest_date"),
        )
        .join(Stock, Stock.id == Price.stock_id)
        .where(
            Stock.is_active.is_(True),
            Stock.instrument_type == "ordinary",
        )
        .group_by(Price.stock_id)
        .subquery()
    )
    latest_session_covered_count = (
        int(
            db.scalar(
                sa.select(sa.func.count())
                .select_from(latest_price_by_stock)
                .where(latest_price_by_stock.c.latest_date == latest_price_date)
            )
            or 0
        )
        if latest_price_date
        else 0
    )

    index_stats = db.execute(
        sa.select(
            sa.func.count(),
            sa.func.min(NEPSEIndex.date),
            sa.func.max(NEPSEIndex.date),
        )
    ).one()

    signal_count_rows = db.execute(
        sa.select(Signal.signal_type, sa.func.count())
        .where(
            Signal.provider == "research_score",
            Signal.model_source == "sector-stock-score-v1",
        )
        .group_by(Signal.signal_type)
    ).all()
    signal_counts = {str(signal_type): int(count) for signal_type, count in signal_count_rows}

    trigger_rows = db.scalars(
        sa.select(Signal).where(
            Signal.provider == "research_score",
            Signal.model_source == "sector-stock-score-v1",
            Signal.signal_type.in_(("BUY_TRIGGER", "SELL_TRIGGER")),
        )
    ).all()
    forward_progress: dict[str, dict[str, dict[str, int]]] = {
        str(horizon): {
            "buy": {"complete": 0, "pending": 0, "review_required": 0},
            "sell": {"complete": 0, "pending": 0, "review_required": 0},
        }
        for horizon in FORWARD_HORIZONS
    }
    for signal in trigger_rows:
        try:
            details = json.loads(signal.rule_params or "{}")
        except (TypeError, ValueError):
            details = {}
        details = details if isinstance(details, dict) else {}
        performance = details.get("forward_performance")
        performance = performance if isinstance(performance, dict) else {}
        outcomes = performance.get("outcomes")
        outcomes = outcomes if isinstance(outcomes, dict) else {}
        direction = "buy" if signal.signal_type == "BUY_TRIGGER" else "sell"
        for horizon in FORWARD_HORIZONS:
            outcome = outcomes.get(str(horizon))
            status = outcome.get("status") if isinstance(outcome, dict) else None
            bucket = (
                "complete"
                if status == "complete"
                else "review_required"
                if status == "review_required"
                else "pending"
            )
            forward_progress[str(horizon)][direction][bucket] += 1

    run_status_rows = db.execute(
        sa.select(AutomationRun.status, sa.func.count())
        .group_by(AutomationRun.status)
    ).all()
    automation_status_counts = {
        str(status): int(count) for status, count in run_status_rows
    }
    latest_run = db.scalars(
        sa.select(AutomationRun)
        .order_by(AutomationRun.started_at.desc(), AutomationRun.id.desc())
        .limit(1)
    ).first()

    return {
        "as_of": as_of.isoformat(),
        "market_data": {
            "active_ordinary_shares": int(active_ordinary_count),
            "shares_with_price_history": covered_ordinary_count,
            "shares_without_price_history": max(
                int(active_ordinary_count) - covered_ordinary_count, 0
            ),
            "coverage_pct": round(
                covered_ordinary_count / active_ordinary_count * 100.0, 2
            )
            if active_ordinary_count
            else 0.0,
            "shares_with_latest_session": latest_session_covered_count,
            "shares_not_at_latest_session": max(
                int(active_ordinary_count) - latest_session_covered_count, 0
            ),
            "latest_session_coverage_pct": round(
                latest_session_covered_count / active_ordinary_count * 100.0, 2
            )
            if active_ordinary_count
            else 0.0,
            "price_rows": price_rows,
            "first_price_date": first_price_date.isoformat() if first_price_date else None,
            "latest_price_date": latest_price_date.isoformat() if latest_price_date else None,
            "latest_price_age_calendar_days": (
                (as_of - latest_price_date).days if latest_price_date else None
            ),
        },
        "nepse_index": {
            "rows": int(index_stats[0] or 0),
            "first_date": index_stats[1].isoformat() if index_stats[1] else None,
            "latest_date": index_stats[2].isoformat() if index_stats[2] else None,
            "latest_age_calendar_days": (
                (as_of - index_stats[2]).days if index_stats[2] else None
            ),
        },
        "signals": {
            "snapshot_count": sum(signal_counts.values()),
            "counts_by_type": signal_counts,
            "buy_trigger_count": signal_counts.get("BUY_TRIGGER", 0),
            "sell_trigger_count": signal_counts.get("SELL_TRIGGER", 0),
        },
        "forward_testing": {
            "trigger_count": len(trigger_rows),
            "horizons": forward_progress,
            "methodology": (
                "BUY uses next-session open entry; SELL models an existing holder's "
                "next-session open exit versus continued holding. Results are not "
                "short-sale returns and remain subject to the raw-price/corporate-action "
                "disclaimer shown in the signal performance endpoint."
            ),
        },
        "automation": {
            "run_count": sum(automation_status_counts.values()),
            "status_counts": automation_status_counts,
            "latest_run": (
                {
                    "job_type": latest_run.job_type,
                    "status": latest_run.status,
                    "started_at": (
                        latest_run.started_at.isoformat()
                        if latest_run.started_at
                        else None
                    ),
                    "completed_at": (
                        latest_run.completed_at.isoformat()
                        if latest_run.completed_at
                        else None
                    ),
                    "error_category": latest_run.error_category,
                }
                if latest_run
                else None
            ),
        },
    }


@router.get("/automation/history")
def get_automation_history(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """Expose compact job status and aggregate counts; never expose automation credentials."""
    rows = db.scalars(
        sa.select(AutomationRun)
        .order_by(AutomationRun.started_at.desc(), AutomationRun.id.desc())
        .limit(limit)
    ).all()
    return {
        "count": len(rows),
        "runs": [
            {
                "id": row.id,
                "job_type": row.job_type,
                "status": row.status,
                "run_key": row.run_key,
                "batch_offset": row.batch_offset,
                "records_processed": row.records_processed,
                "records_inserted": row.records_inserted,
                "records_updated": row.records_updated,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "summary": row.summary or {},
                "error_category": row.error_category,
            }
            for row in rows
        ],
    }
