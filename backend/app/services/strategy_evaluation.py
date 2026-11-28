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
    sharpe = _number(metrics.get("sharpe_ratio"))
    sortino = _number(metrics.get("sortino_ratio"))
    calmar = _number(metrics.get("calmar_ratio"))
    profit_factor = _number(metrics.get("profit_factor"))
    drawdown = max(0.0, _number(metrics.get("max_drawdown")))

    def clamp(value: float) -> float:
        return max(0.0, min(100.0, value))

    components = {
        "sharpe": clamp(((sharpe + 1.0) / 4.0) * 100.0),
        "sortino": clamp((sortino / 4.0) * 100.0),
        "calmar": clamp((calmar / 2.0) * 100.0),
        "profit_factor": clamp((profit_factor / 2.0) * 100.0),
        "drawdown_control": clamp(((0.40 - drawdown) / 0.40) * 100.0),
    }
    weights = {
        "sharpe": 0.25,
        "sortino": 0.20,
        "calmar": 0.20,
        "profit_factor": 0.20,
        "drawdown_control": 0.15,
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
    nested = payload.get("metrics")
    return nested if isinstance(nested, dict) else payload


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
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("metrics")
    return nested if isinstance(nested, dict) else payload
