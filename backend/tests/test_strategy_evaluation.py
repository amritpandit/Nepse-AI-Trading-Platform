"""Tests for chronological strategy holdout evaluation and ranking."""

from app.models.backtest import Backtest
from app.models.strategy import Strategy
from app.services.strategy_evaluation import (
    StrategyEvaluationService,
    _derive_performance_metrics,
    score_holdout_metrics,
)
from sqlalchemy.orm import Session


def test_holdout_score_is_bounded_and_sample_gated() -> None:
    eligible = score_holdout_metrics(
        {
            "sharpe_ratio": 1.2,
            "sortino_ratio": 1.8,
            "calmar_ratio": 0.9,
            "profit_factor": 1.6,
            "max_drawdown": 0.12,
            "observed_sessions": 45,
            "closed_round_trips": 14,
        }
    )
    assert 0 <= eligible["quality_score"] <= 100
    assert eligible["rank_eligible"] is True
    assert eligible["sample_status"] == "eligible"
    assert set(eligible["score_components"]) == {
        "annualized_return",
        "sharpe",
        "sortino",
        "calmar",
        "profit_factor",
        "drawdown_control",
        "expectancy",
        "turnover_efficiency",
    }

    insufficient = score_holdout_metrics(
        {
            "sharpe_ratio": 3.0,
            "sortino_ratio": 4.0,
            "calmar_ratio": 2.0,
            "profit_factor": 5.0,
            "max_drawdown": 0.02,
            "observed_sessions": 19,
            "closed_round_trips": 50,
        }
    )
    assert insufficient["rank_eligible"] is False
    assert insufficient["sample_status"] == "insufficient_sample"


def test_derived_metrics_cover_risk_exposure_turnover_and_closed_trades() -> None:
    payload = {
        "metrics": {
            "total_return": 0.10,
            "annualized_return": 0.20,
            "max_drawdown": 0.10,
            "sharpe_ratio": 1.0,
            "profit_factor": 1.5,
            "expectancy": 500.0,
        },
        "equity_curve": [
            {"date": "2024-01-01", "equity": 100000, "positions_value": 0},
            {"date": "2024-01-02", "equity": 105000, "positions_value": 50000},
            {"date": "2024-01-03", "equity": 99000, "positions_value": 45000},
            {"date": "2024-01-04", "equity": 110000, "positions_value": 0},
        ],
        "trades": [
            {
                "symbol": "TEST",
                "action": "BUY",
                "quantity": 100,
                "price": 100,
                "transaction_cost": 50,
                "timestamp": "2024-01-02T09:00:00",
            },
            {
                "symbol": "TEST",
                "action": "SELL",
                "quantity": 100,
                "price": 110,
                "transaction_cost": 55,
                "timestamp": "2024-01-04T09:00:00",
            },
        ],
    }

    metrics = _derive_performance_metrics(payload)
    assert metrics["observed_sessions"] == 4
    assert metrics["closed_round_trips"] == 1
    assert metrics["gross_profit"] == 895.0
    assert metrics["market_exposure_pct"] == 0.5
    assert metrics["turnover_ratio"] > 0
    assert metrics["annualized_volatility"] >= 0
    assert metrics["sortino_ratio"] is not None
    assert metrics["calmar_ratio"] == 2.0


def test_leaderboard_groups_saved_train_and_holdout_runs(db_session: Session) -> None:
    strategy = Strategy(
        name="Frozen momentum",
        version="1.2",
        config={"symbols": ["TEST"], "entry_rules": [], "exit_rules": []},
    )
    db_session.add(strategy)
    db_session.flush()

    evaluation_id = "evaluation-test-001"
    shared = {
        "evaluation_id": evaluation_id,
        "evaluation_method": "frozen_rule_chronological_holdout",
        "train_ratio": 0.70,
        "train_sessions": 70,
        "holdout_sessions": 30,
        "full_start_date": "2024-01-01",
        "full_end_date": "2024-06-30",
    }
    summary = {
        "total_return": 0.12,
        "annualized_return": 0.24,
        "max_drawdown": 0.08,
        "sharpe_ratio": 1.1,
        "profit_factor": 1.7,
    }
    curve = [
        {"date": "2024-04-01", "equity": 100000, "positions_value": 0},
        {"date": "2024-04-02", "equity": 101000, "positions_value": 50000},
    ]
    trades: list[dict[str, object]] = []
    db_session.add_all(
        [
            Backtest(
                strategy_id=strategy.id,
                config={
                    **shared,
                    "evaluation_segment": "train",
                    "start_date": "2024-01-01",
                    "end_date": "2024-03-31",
                },
                metrics={"metrics": summary, "equity_curve": curve, "trades": trades},
            ),
            Backtest(
                strategy_id=strategy.id,
                config={
                    **shared,
                    "evaluation_segment": "holdout",
                    "start_date": "2024-04-01",
                    "end_date": "2024-06-30",
                },
                metrics={"metrics": summary, "equity_curve": curve, "trades": trades},
            ),
        ]
    )
    db_session.commit()

    rows = StrategyEvaluationService(session=db_session).list_evaluations()
    assert len(rows) == 1
    assert rows[0]["evaluation_id"] == evaluation_id
    assert rows[0]["strategy_name"] == "Frozen momentum"
    assert rows[0]["train"]["start_date"] == "2024-01-01"
    assert rows[0]["holdout"]["start_date"] == "2024-04-01"
    assert rows[0]["rank_eligible"] is False



def test_holdout_evaluation_uses_later_observed_sessions_and_freezes_config(
    db_session: Session,
) -> None:
    from datetime import date, timedelta
    from unittest.mock import patch

    from app.services.backtest import BacktestEngine, BacktestService

    strategy = Strategy(
        name="Frozen rule test",
        version="2.0",
        config={"symbols": ["TEST"], "entry_rules": [], "exit_rules": []},
    )
    db_session.add(strategy)
    db_session.commit()

    observed_dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(80)]
    saved_configs: list[dict] = []

    def fake_run_backtest(
        service: BacktestService, strategy_id: int, run_config: dict
    ) -> dict:
        saved_configs.append(dict(run_config))
        return {
            "backtest_id": len(saved_configs),
            "strategy_id": strategy_id,
            "metrics": {
                "metrics": {
                    "total_return": 0.05,
                    "annualized_return": 0.10,
                    "max_drawdown": 0.04,
                    "sharpe_ratio": 1.0,
                    "profit_factor": 1.2,
                },
                "equity_curve": [],
                "trades": [],
            },
        }

    with (
        patch.object(BacktestEngine, "_get_trading_dates", return_value=observed_dates),
        patch.object(BacktestService, "run_backtest", fake_run_backtest),
    ):
        result = StrategyEvaluationService(session=db_session).run_holdout_evaluation(
            strategy.id,
            {
                "start_date": observed_dates[0].isoformat(),
                "end_date": observed_dates[-1].isoformat(),
                "initial_capital": 1000000,
                "commission_rate": 0.005,
                "slippage_bps": 5,
                "execution_delay_bars": 1,
            },
        )

    assert len(saved_configs) == 2
    assert saved_configs[0]["evaluation_segment"] == "train"
    assert saved_configs[1]["evaluation_segment"] == "holdout"
    assert saved_configs[0]["end_date"] < saved_configs[1]["start_date"]
    assert saved_configs[0]["evaluation_id"] == saved_configs[1]["evaluation_id"]
    assert saved_configs[0]["evaluation_method"] == "frozen_rule_chronological_holdout"
    assert result["train"]["observed_sessions"] == 56
    assert result["holdout"]["observed_sessions"] == 24
    assert result["rank_eligible"] is False
