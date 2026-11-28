"""Regression tests for realistic portfolio backtest execution and metrics."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from app.models.backtest import Backtest
from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.stock import Stock
from app.services.backtest import BacktestEngine
from app.services.benchmark import BenchmarkService
from sqlalchemy import select
from sqlalchemy.orm import Session
from pytest import MonkeyPatch


class _AllowBacktestGate:
    def assert_safe_for_backtest(self, symbol: str, date_str: str) -> None:
        del symbol, date_str


def _run_engine(
    engine: BacktestEngine,
    monkeypatch: MonkeyPatch,
    sessions: list[date],
    prices_by_date: dict[str, dict[str, Any]],
    rsi_by_date: dict[str, float],
) -> dict[str, Any]:
    monkeypatch.setattr(
        engine,
        "_get_trading_dates",
        lambda start_date, end_date, symbols: sessions,
    )
    monkeypatch.setattr(
        engine,
        "_get_price",
        lambda symbol, date_str: prices_by_date.get(date_str),
    )
    monkeypatch.setattr(
        engine,
        "_get_features",
        lambda symbol, date_str: {"rsi_14": rsi_by_date[date_str]},
    )
    monkeypatch.setattr(engine, "gate", _AllowBacktestGate())

    strategy = SimpleNamespace(
        name="execution-integrity",
        version="1.0",
        config={
            "entry_rules": [
                {"rule": "rsi_oversold", "params": {"threshold": 30}}
            ],
            "exit_rules": [
                {"rule": "rsi_exit_long", "params": {"threshold": 70}}
            ],
            "risk_rules": [],
        },
    )
    return engine.run(
        strategy=strategy,  # type: ignore[arg-type]
        symbols=["NABIL"],
        start_date=sessions[0].isoformat(),
        end_date=sessions[-1].isoformat(),
    )


def test_eod_exit_executes_next_session_open_and_partial_sells_retain_position(
    monkeypatch: MonkeyPatch,
) -> None:
    sessions = [date(2024, 1, 1) + timedelta(days=i) for i in range(5)]
    dates = [item.isoformat() for item in sessions]
    opens = [100.0, 102.0, 110.0, 111.0, 112.0]
    closes = [101.0, 103.0, 109.0, 110.0, 111.0]
    volumes = [1000, 1000, 100, 100, 200]
    prices = {
        day: {
            "open": opening,
            "high": max(opening, close),
            "low": min(opening, close),
            "close": close,
            "volume": volume,
        }
        for day, opening, close, volume in zip(
            dates, opens, closes, volumes, strict=True
        )
    }
    rsi = {
        dates[0]: 25.0,  # Entry signal from the first session's completed close.
        dates[1]: 80.0,  # Exit signal from the next session's completed close.
        dates[2]: 80.0,
        dates[3]: 80.0,
        dates[4]: 80.0,
    }
    engine = BacktestEngine(
        initial_capital=Decimal("100000"),
        commission_rate=Decimal("0"),
        slippage_bps=Decimal("0"),
        min_volume_threshold=100,
        execution_delay_bars=1,
    )

    result = _run_engine(engine, monkeypatch, sessions, prices, rsi)

    trades = result["trades"]
    assert [(item["action"], item["quantity"]) for item in trades] == [
        ("BUY", 100.0),
        ("SELL", 30.0),
        ("SELL", 30.0),
        ("SELL", 40.0),
    ]
    # The closing-indicator exit at Jan 2 cannot fill at Jan 2's close.
    assert trades[0]["timestamp"] == "2024-01-02T09:00:00"
    assert trades[1]["timestamp"] == "2024-01-03T09:00:00"
    assert trades[1]["price"] == 110.0
    assert trades[2]["timestamp"] == "2024-01-04T09:00:00"
    assert trades[3]["timestamp"] == "2024-01-05T09:00:00"

    curve = result["equity_curve"]
    assert curve[1]["positions"]["NABIL"]["quantity"] == 100
    assert curve[2]["positions"]["NABIL"]["quantity"] == 70
    assert curve[3]["positions"]["NABIL"]["quantity"] == 40
    assert curve[4]["positions"] == {}


def test_pending_entry_waits_for_a_real_open_instead_of_using_same_day_close(
    monkeypatch: MonkeyPatch,
) -> None:
    sessions = [date(2024, 1, 1) + timedelta(days=i) for i in range(3)]
    dates = [item.isoformat() for item in sessions]
    prices = {
        dates[0]: {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1000,
        },
        dates[1]: {
            "open": None,
            "high": 121.0,
            "low": 119.0,
            "close": 120.0,
            "volume": 1000,
        },
        dates[2]: {
            "open": 130.0,
            "high": 131.0,
            "low": 129.0,
            "close": 130.0,
            "volume": 1000,
        },
    }
    rsi = {dates[0]: 25.0, dates[1]: 50.0, dates[2]: 50.0}
    engine = BacktestEngine(
        initial_capital=Decimal("100000"),
        commission_rate=Decimal("0"),
        slippage_bps=Decimal("0"),
        min_volume_threshold=100,
        execution_delay_bars=1,
    )

    result = _run_engine(engine, monkeypatch, sessions, prices, rsi)

    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade["action"] == "BUY"
    assert trade["timestamp"] == "2024-01-03T09:00:00"
    assert trade["price"] == 130.0


def test_zero_execution_delay_is_normalized_to_next_session() -> None:
    engine = BacktestEngine(execution_delay_bars=0)
    assert engine.execution_delay_bars == 1


def test_annualized_return_is_compounded_over_observed_sessions() -> None:
    engine = BacktestEngine()
    equity_curve = [
        {
            "date": (date(2024, 1, 1) + timedelta(days=i)).isoformat(),
            "equity": 1000.0 * (1.21 ** (i / 252)),
        }
        for i in range(253)
    ]

    assert abs(engine._calculate_annualized_return(equity_curve) - 0.21) < 1e-9
    assert engine._calculate_annualized_return(equity_curve[:1]) == 0.0
    assert engine._calculate_annualized_return(
        [{**equity_curve[0], "equity": 1000.0}, {**equity_curve[-1], "equity": 0.0}]
    ) == -1.0


def test_benchmark_annualized_return_uses_compounded_sessions() -> None:
    equity_curve = [
        {
            "date": (date(2024, 1, 1) + timedelta(days=i)).isoformat(),
            "equity": 1000.0 * (1.21 ** (i / 252)),
            "cash": 0.0,
            "positions_value": 1000.0 * (1.21 ** (i / 252)),
        }
        for i in range(253)
    ]

    result = BenchmarkService()._calculate_benchmark_metrics(equity_curve)

    assert abs(result["annualized_return"] - 0.21) < 1e-6


def test_backtest_snapshots_persist_holdings_detail(db_session: Session) -> None:
    from app.models.strategy import Strategy

    stock = Stock(symbol="NABIL", instrument_type="ordinary", is_active=True)
    strategy = Strategy(name="snapshot", version="1.0", config={"symbols": ["NABIL"]})
    db_session.add_all([stock, strategy])
    db_session.flush()
    backtest = Backtest(strategy_id=strategy.id, config={})
    db_session.add(backtest)
    db_session.commit()

    engine = BacktestEngine()
    engine._persist_trades_and_snapshots(
        db_session,
        backtest.id,
        [
            {
                "date": "2024-01-01",
                "equity": 1000.0,
                "cash": 500.0,
                "positions_value": 500.0,
                "positions": {
                    "NABIL": {
                        "quantity": 5,
                        "entry_price": 90.0,
                        "mark_price": 100.0,
                        "market_value": 500.0,
                        "unrealized_pnl": 50.0,
                        "entry_date": "2023-12-20",
                    }
                },
            }
        ],
        [],
    )

    snapshot = db_session.scalar(
        select(PortfolioSnapshot).where(PortfolioSnapshot.backtest_id == backtest.id)
    )
    assert snapshot is not None
    assert snapshot.positions["NABIL"]["quantity"] == 5
    assert snapshot.positions["NABIL"]["unrealized_pnl"] == 50.0
