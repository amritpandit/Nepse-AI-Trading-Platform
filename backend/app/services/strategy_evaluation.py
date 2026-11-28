"""Chronological holdout evaluation and transparent strategy ranking.

A holdout run applies the strategy's already-saved rules unchanged to a later,
contiguous block of observed NEPSE sessions. It does not optimize parameters or
claim that a strategy was trained by the earlier segment.
"""

from __future__ import annotations

import math
import uuid
from datetime import date
from typing import Any

import sqlalchemy as sa
from app.db.session import session_scope
from app.models.backtest import Backtest
from app.models.strategy import Strategy
from app.services.backtest import BacktestEngine, BacktestService
from sqlalchemy.orm import Session


MIN_TRAIN_SESSIONS = 20
MIN_HOLDOUT_SESSIONS = 20
MIN_CLOSED_ROUND_TRIPS = 10


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def score_holdout_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Create a bounded, explainable quality score from holdout metrics only."""
    annualized_return = _number(metrics.get("annualized_return"))
    sharpe = _number(metrics.get("sharpe_ratio"))
    sortino = _number(metrics.get("sortino_ratio"))
    calmar = _number(metrics.get("calmar_ratio"))
    profit_factor = _number(
        metrics.get("profit_factor_score_input", metrics.get("profit_factor"))
    )
    drawdown = max(0.0, _number(metrics.get("max_drawdown")))
    expectancy_return = _number(metrics.get("expectancy_return_pct"))
    turnover_ratio = max(0.0, _number(metrics.get("turnover_ratio")))

    def clamp(value: float) -> float:
        return max(0.0, min(100.0, value))

    components = {
        "annualized_return": clamp(((annualized_return + 0.05) / 0.30) * 100.0),
        "sharpe": clamp(((sharpe + 1.0) / 4.0) * 100.0),
        "sortino": clamp((sortino / 4.0) * 100.0),
        "calmar": clamp((calmar / 2.0) * 100.0),
        "profit_factor": clamp((profit_factor / 2.0) * 100.0),
        "drawdown_control": clamp(((0.40 - drawdown) / 0.40) * 100.0),
        "expectancy": clamp(((expectancy_return + 0.005) / 0.025) * 100.0),
        "turnover_efficiency": clamp(100.0 / (1.0 + turnover_ratio / 2.0)),
    }
    weights = {
        "annualized_return": 0.15,
        "sharpe": 0.15,
        "sortino": 0.10,
        "calmar": 0.15,
        "profit_factor": 0.15,
        "drawdown_control": 0.10,
        "expectancy": 0.10,
        "turnover_efficiency": 0.10,
    }
    quality_score = round(
        sum(components[key] * weights[key] for key in weights), 2
    )
    sessions = int(_number(metrics.get("observed_sessions")))
    closed_round_trips = int(_number(metrics.get("closed_round_trips")))
    eligible = (
        sessions >= MIN_HOLDOUT_SESSIONS
        and closed_round_trips >= MIN_CLOSED_ROUND_TRIPS
    )
    return {
        "quality_score": quality_score,
        "score_components": components,
        "score_weights": weights,
        "rank_eligible": eligible,
        "sample_status": "eligible" if eligible else "insufficient_sample",
        "sample_requirements": {
            "minimum_holdout_sessions": MIN_HOLDOUT_SESSIONS,
            "minimum_closed_round_trips": MIN_CLOSED_ROUND_TRIPS,
        },
    }


def _metrics_from_backtest(backtest: Backtest) -> dict[str, Any]:
    payload = backtest.metrics if isinstance(backtest.metrics, dict) else {}
    return _derive_performance_metrics(payload)


class StrategyEvaluationService:
    """Runs and lists persisted frozen-rule chronological holdout evaluations."""

    def __init__(self, session: Session | None = None) -> None:
        self._session = session

    def run_holdout_evaluation(
        self,
        strategy_id: int,
        config: dict[str, Any],
        train_ratio: float = 0.70,
    ) -> dict[str, Any]:
        if not 0.50 <= train_ratio <= 0.80:
            raise ValueError("train_ratio must be between 0.50 and 0.80")

        start_date = date.fromisoformat(str(config["start_date"]))
        end_date = date.fromisoformat(str(config["end_date"]))
        if start_date >= end_date:
            raise ValueError("start_date must be before end_date")

        with session_scope(self._session) as session:
            strategy = session.get(Strategy, strategy_id)
            if strategy is None:
                raise ValueError(f"Strategy {strategy_id} not found")
            symbols = strategy.config.get("symbols", [])
            if not symbols:
                raise ValueError("Strategy has no symbols configured")

            engine = BacktestEngine()
            observed_dates = engine._get_trading_dates(
                start_date.isoformat(), end_date.isoformat(), symbols
            )

        split_index = int(len(observed_dates) * train_ratio)
        train_dates = observed_dates[:split_index]
        holdout_dates = observed_dates[split_index:]
        if len(train_dates) < MIN_TRAIN_SESSIONS:
            raise ValueError(
                f"At least {MIN_TRAIN_SESSIONS} observed NEPSE sessions are required "
                "in the training segment"
            )
        if len(holdout_dates) < MIN_HOLDOUT_SESSIONS:
            raise ValueError(
                f"At least {MIN_HOLDOUT_SESSIONS} observed NEPSE sessions are required "
                "in the holdout segment; widen the date range or adjust the split"
            )

        evaluation_id = str(uuid.uuid4())
        evaluation_config = dict(config)
        evaluation_config.update(
            {
                "evaluation_id": evaluation_id,
                "evaluation_method": "frozen_rule_chronological_holdout",
                "train_ratio": train_ratio,
                "train_sessions": len(train_dates),
                "holdout_sessions": len(holdout_dates),
                "full_start_date": start_date.isoformat(),
                "full_end_date": end_date.isoformat(),
                "strategy_version": strategy.version,
            }
        )

        backtest_service = BacktestService(session=self._session)
        train_config = dict(evaluation_config)
        train_config.update(
            {
                "start_date": train_dates[0].isoformat(),
                "end_date": train_dates[-1].isoformat(),
                "evaluation_segment": "train",
            }
        )
        holdout_config = dict(evaluation_config)
        holdout_config.update(
            {
                "start_date": holdout_dates[0].isoformat(),
                "end_date": holdout_dates[-1].isoformat(),
                "evaluation_segment": "holdout",
            }
        )

        train_result = backtest_service.run_backtest(strategy_id, train_config)
        holdout_result = backtest_service.run_backtest(strategy_id, holdout_config)
        train_metrics = _metrics_from_payload(train_result)
        holdout_metrics = _metrics_from_payload(holdout_result)
        holdout_metrics = dict(holdout_metrics)
        holdout_metrics["observed_sessions"] = len(holdout_dates)
        score = score_holdout_metrics(holdout_metrics)

        return {
            "evaluation_id": evaluation_id,
            "strategy_id": strategy_id,
            "strategy_name": strategy.name,
            "strategy_version": strategy.version,
            "method": "frozen_rule_chronological_holdout",
            "train_ratio": train_ratio,
            "train": {
                "backtest_id": train_result.get("backtest_id"),
                "start_date": train_dates[0].isoformat(),
                "end_date": train_dates[-1].isoformat(),
                "observed_sessions": len(train_dates),
                "metrics": train_metrics,
            },
            "holdout": {
                "backtest_id": holdout_result.get("backtest_id"),
                "start_date": holdout_dates[0].isoformat(),
                "end_date": holdout_dates[-1].isoformat(),
                "observed_sessions": len(holdout_dates),
                "metrics": holdout_metrics,
            },
            **score,
            "interpretation": (
                "The strategy rules and stored version were held unchanged across "
                "the chronological split. The holdout simulation starts with fresh "
                "cash and no inherited positions. This is a historical research "
                "test, not a guarantee of future performance."
            ),
        }

    def list_evaluations(self, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with session_scope(self._session) as session:
            rows = session.scalars(
                sa.select(Backtest)
                .where(Backtest.config.is_not(None))
                .order_by(Backtest.created_at.desc())
                .limit(1000)
            ).all()
            strategies = {
                item.id: item
                for item in session.scalars(
                    sa.select(Strategy).where(
                        Strategy.id.in_(
                            {row.strategy_id for row in rows if row.strategy_id is not None}
                        )
                    )
                ).all()
            }

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            config = row.config if isinstance(row.config, dict) else {}
            evaluation_id = config.get("evaluation_id")
            segment = config.get("evaluation_segment")
            if not evaluation_id or segment not in {"train", "holdout"}:
                continue
            strategy = strategies.get(row.strategy_id)
            item = grouped.setdefault(
                str(evaluation_id),
                {
                    "evaluation_id": str(evaluation_id),
                    "strategy_id": row.strategy_id,
                    "strategy_name": strategy.name if strategy else "Unknown strategy",
                    "strategy_version": strategy.version if strategy else None,
                    "created_at": (
                        row.created_at.isoformat() if row.created_at else None
                    ),
                    "method": config.get("evaluation_method"),
                    "train_ratio": _number(config.get("train_ratio"), 0.70),
                    "full_start_date": config.get("full_start_date"),
                    "full_end_date": config.get("full_end_date"),
                },
            )
            item[segment] = {
                "backtest_id": row.id,
                "start_date": config.get("start_date"),
                "end_date": config.get("end_date"),
                "observed_sessions": int(
                    _number(
                        config.get(
                            "train_sessions" if segment == "train" else "holdout_sessions"
                        )
                    )
                ),
                "metrics": _metrics_from_backtest(row),
            }

        evaluations: list[dict[str, Any]] = []
        for item in grouped.values():
            holdout = item.get("holdout")
            if not holdout:
                continue
            holdout_metrics = dict(holdout["metrics"])
            holdout_metrics["observed_sessions"] = holdout.get("observed_sessions", 0)
            item.update(score_holdout_metrics(holdout_metrics))
            item["holdout_return"] = _number(holdout_metrics.get("total_return"))
            item["holdout_cagr"] = _number(holdout_metrics.get("annualized_return"))
            item["holdout_max_drawdown"] = _number(
                holdout_metrics.get("max_drawdown")
            )
            item["holdout_sharpe"] = holdout_metrics.get("sharpe_ratio")
            item["holdout_profit_factor"] = holdout_metrics.get("profit_factor")
            item["holdout_closed_round_trips"] = int(
                _number(holdout_metrics.get("closed_round_trips"))
            )
            item["holdout_expectancy"] = holdout_metrics.get("expectancy")
            item["holdout_turnover_ratio"] = holdout_metrics.get("turnover_ratio")
            evaluations.append(item)

        evaluations.sort(
            key=lambda item: (
                bool(item.get("rank_eligible")),
                _number(item.get("quality_score")),
                str(item.get("created_at") or ""),
            ),
            reverse=True,
        )
        return evaluations[:safe_limit]


def _metrics_from_payload(result: dict[str, Any]) -> dict[str, Any]:
    payload = result.get("metrics")
    return _derive_performance_metrics(payload if isinstance(payload, dict) else {})


def _derive_performance_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    """Derive supplemental risk, exposure and trade-quality metrics from persisted data."""
    nested = payload.get("metrics")
    metrics = dict(nested) if isinstance(nested, dict) else dict(payload)
    curve = payload.get("equity_curve")
    trades = payload.get("trades")
    equity_curve = curve if isinstance(curve, list) else []
    trade_rows = trades if isinstance(trades, list) else []

    daily_returns: list[float] = []
    equity_values: list[float] = []
    exposure_values: list[float] = []
    invested_sessions = 0
    previous_equity: float | None = None
    for point in equity_curve:
        if not isinstance(point, dict):
            continue
        equity = _number(point.get("equity"))
        positions_value = max(0.0, _number(point.get("positions_value")))
        equity_values.append(equity)
        exposure_values.append(positions_value / equity if equity > 0 else 0.0)
        if positions_value > 0:
            invested_sessions += 1
        if previous_equity is not None and previous_equity > 0:
            daily_returns.append((equity - previous_equity) / previous_equity)
        previous_equity = equity

    if len(daily_returns) > 1:
        mean_return = sum(daily_returns) / len(daily_returns)
        variance = sum((value - mean_return) ** 2 for value in daily_returns) / (
            len(daily_returns) - 1
        )
        annualized_volatility = math.sqrt(max(0.0, variance)) * math.sqrt(252.0)
    else:
        mean_return = sum(daily_returns) / len(daily_returns) if daily_returns else 0.0
        annualized_volatility = 0.0

    downside_deviation = math.sqrt(
        sum(min(value, 0.0) ** 2 for value in daily_returns) / len(daily_returns)
    ) if daily_returns else 0.0
    sortino = (
        mean_return / downside_deviation * math.sqrt(252.0)
        if downside_deviation > 0
        else None
    )
    max_drawdown = max(0.0, _number(metrics.get("max_drawdown")))
    annualized_return = _number(metrics.get("annualized_return"))
    calmar = annualized_return / max_drawdown if max_drawdown > 0 else None

    # FIFO match actual executed fills into closed round trips. Unclosed inventory is
    # excluded from trade expectancy and profit factor; its value remains in equity.
    open_lots: dict[str, list[list[float]]] = {}
    closed_pnls: list[float] = []
    total_notional = 0.0
    for trade in sorted(
        (item for item in trade_rows if isinstance(item, dict)),
        key=lambda item: str(item.get("timestamp", "")),
    ):
        symbol = str(trade.get("symbol", "")).upper()
        action = str(trade.get("action", "")).upper()
        quantity = _number(trade.get("quantity"))
        price = _number(trade.get("price"))
        fee = _number(trade.get("transaction_cost"))
        if not symbol or quantity <= 0 or price <= 0:
            continue
        total_notional += quantity * price
        fee_per_share = fee / quantity
        if action == "BUY":
            open_lots.setdefault(symbol, []).append([quantity, price, fee_per_share])
        elif action == "SELL":
            remaining = quantity
            lots = open_lots.setdefault(symbol, [])
            while remaining > 0 and lots:
                lot = lots[0]
                matched = min(remaining, lot[0])
                gross = (price - lot[1]) * matched
                fees = (fee_per_share + lot[2]) * matched
                closed_pnls.append(gross - fees)
                lot[0] -= matched
                remaining -= matched
                if lot[0] <= 1e-9:
                    lots.pop(0)

    winners = [value for value in closed_pnls if value > 0]
    losers = [value for value in closed_pnls if value < 0]
    average_equity = sum(equity_values) / len(equity_values) if equity_values else 0.0
    gross_profit_value = sum(winners)
    gross_loss_value = abs(sum(losers))
    closed_count = len(closed_pnls)
    profit_factor_value = (
        gross_profit_value / gross_loss_value if gross_loss_value > 0 else None
    )
    metrics.update(
        {
            "observed_sessions": len(equity_curve),
            "annualized_volatility": round(annualized_volatility, 6),
            "sortino_ratio": round(sortino, 4) if sortino is not None else None,
            "calmar_ratio": round(calmar, 4) if calmar is not None else None,
            "closed_round_trips": len(closed_pnls),
            "gross_profit": round(gross_profit_value, 4),
            "gross_loss": round(gross_loss_value, 4),
            "profit_factor": (
                round(profit_factor_value, 4) if profit_factor_value is not None else None
            ),
            "profit_factor_score_input": (
                min(5.0, profit_factor_value)
                if profit_factor_value is not None
                else (5.0 if closed_count > 0 and gross_profit_value > 0 else 0.0)
            ),
            "average_win": round(sum(winners) / len(winners), 4) if winners else None,
            "average_loss": round(sum(losers) / len(losers), 4) if losers else None,
            "win_rate": round(len(winners) / closed_count, 6) if closed_count else 0.0,
            "expectancy": (
                round(sum(closed_pnls) / closed_count, 4) if closed_count else 0.0
            ),
            "expectancy_return_pct": (
                round((sum(closed_pnls) / closed_count) / average_equity, 6)
                if closed_count and average_equity > 0
                else 0.0
            ),
            "average_equity": round(average_equity, 4),
            "turnover_ratio": round(total_notional / average_equity, 6)
            if average_equity > 0
            else None,
            "market_exposure_pct": round(invested_sessions / len(equity_curve), 6)
            if equity_curve
            else 0.0,
            "average_exposure_pct": round(
                sum(exposure_values) / len(exposure_values), 6
            )
            if exposure_values
            else 0.0,
        }
    )
    return metrics
