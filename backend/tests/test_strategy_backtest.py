"""Tests for Phase 5: Strategy and Backtesting MVP."""

from datetime import date, timedelta
from decimal import Decimal

import polars as pl
from app.models.benchmark import NEPSEIndex, SectorIndex
from app.models.price import Price
from app.models.stock import Stock
from app.models.strategy import Strategy
from app.models.trade import Trade
from app.services.backtest import BacktestEngine, BacktestService, VectorizedBacktestEngine
from app.services.benchmark import BenchmarkService
from app.services.strategy import StrategyService
from sqlalchemy import select
from sqlalchemy.orm import Session


def _seed_strategy(
    db: Session,
    name: str = "test_strategy",
    version: str = "v1.0.0",
    config: dict | None = None,
) -> Strategy:
    if config is None:
        config = {
            "symbols": ["TEST"],
            "entry_rules": [{"rule": "rsi_oversold", "params": {"threshold": 30}}],
            "exit_rules": [{"rule": "rsi_exit_long", "params": {"threshold": 70}}],
            "risk_rules": [{"rule": "max_position_size", "params": {"max_pct": 0.1}}],
        }
    strategy = Strategy(name=name, version=version, config=config)
    db.add(strategy)
    db.flush()
    db.refresh(strategy)
    return strategy


def _seed_price_data(
    db: Session,
    symbol: str,
    start_date: str,
    count: int = 60,
    base_price: float = 100.0,
) -> int:
    stock = db.scalar(select(Stock).where(Stock.symbol == symbol.upper()))
    if stock is None:
        stock = Stock(symbol=symbol.upper(), is_active=True)
        db.add(stock)
        db.flush()

    start = date.fromisoformat(start_date)
    for i in range(count):
        price_date = start + timedelta(days=i)
        close = base_price + (i - count / 2) * 0.5
        high = close + 2.0
        low = close - 2.0
        db.add(
            Price(
                stock_id=stock.id,
                date=price_date,
                open=Decimal(str(close * 0.99)),
                high=Decimal(str(high)),
                low=Decimal(str(low)),
                close=Decimal(str(close)),
                volume=1000 + i * 10,
            )
        )
    db.flush()
    return stock.id


class TestStrategyService:
    def test_create_strategy(self, db_session: Session) -> None:
        service = StrategyService(session=db_session)
        strategy = service.create_strategy(
            name="momentum",
            version="v1.0.0",
            config={"symbols": ["NEPSE"]},
        )

        assert strategy.id is not None
        assert strategy.name == "momentum"
        assert strategy.version == "v1.0.0"

    def test_get_strategy(self, db_session: Session) -> None:
        strategy = _seed_strategy(db_session)

        service = StrategyService(session=db_session)
        retrieved = service.get_strategy(strategy.id)

        assert retrieved is not None
        assert retrieved.id == strategy.id

    def test_list_strategies(self, db_session: Session) -> None:
        _seed_strategy(db_session, "strat1")
        _seed_strategy(db_session, "strat2")

        service = StrategyService(session=db_session)
        strategies = service.list_strategies(limit=10)

        assert len(strategies) >= 2

    def test_evaluate_entry_signal_rsi_oversold(self) -> None:
        service = StrategyService()
        features = {"rsi_14": 25.0, "close": 100.0}
        entry_rules = [{"rule": "rsi_oversold", "params": {"threshold": 30}}]

        result = service.evaluate_entry_signal("TEST", "2024-01-01", features, entry_rules)
        assert result is True

    def test_evaluate_entry_signal_rsi_not_oversold(self) -> None:
        service = StrategyService()
        features = {"rsi_14": 50.0, "close": 100.0}
        entry_rules = [{"rule": "rsi_oversold", "params": {"threshold": 30}}]

        result = service.evaluate_entry_signal("TEST", "2024-01-01", features, entry_rules)
        assert result is False

    def test_evaluate_exit_signal(self) -> None:
        service = StrategyService()
        features = {"rsi_14": 75.0, "close": 110.0}
        exit_rules = [{"rule": "rsi_exit_long", "params": {"threshold": 70}}]

        class MockPosition:
            quantity = 100

        result = service.evaluate_exit_signal(
            "TEST", "2024-01-01", features, exit_rules, MockPosition()
        )
        assert result is True

    def test_check_risk_rules(self) -> None:
        service = StrategyService()
        portfolio = {"cash": 1000000, "price": 100.0}
        risk_rules = [{"rule": "max_position_size", "params": {"max_pct": 0.1}}]

        result = service.check_risk_rules(portfolio, risk_rules, "TEST")
        assert "max_quantity" in result
        assert result["max_quantity"] > 0


class TestBacktestEngine:
    def test_apply_slippage_buy(self) -> None:
        engine = BacktestEngine(slippage_bps=Decimal("5.0"))
        price = Decimal("100.0")

        result = engine._apply_slippage(price, "buy")
        assert result > price
        assert result == Decimal("100.05")

    def test_apply_slippage_sell(self) -> None:
        engine = BacktestEngine(slippage_bps=Decimal("5.0"))
        price = Decimal("100.0")

        result = engine._apply_slippage(price, "sell")
        assert result < price
        assert result == Decimal("99.95")

    def test_calculate_fill_rate_high_volume(self) -> None:
        engine = BacktestEngine(partial_fill_threshold=Decimal("0.3"))
        result = engine._calculate_fill_rate(10000, 100)
        assert result == Decimal("1.0")

    def test_calculate_fill_rate_low_volume(self) -> None:
        engine = BacktestEngine(partial_fill_threshold=Decimal("0.3"))
        result = engine._calculate_fill_rate(100, 500)
        assert result < Decimal("1.0")

    def test_calculate_max_drawdown(self) -> None:
        engine = BacktestEngine()
        equity_curve = [
            {"equity": 100000, "cash": 0, "positions_value": 100000},
            {"equity": 90000, "cash": 0, "positions_value": 90000},
            {"equity": 110000, "cash": 0, "positions_value": 110000},
        ]

        result = engine._calculate_max_drawdown(equity_curve)
        assert result == 0.1

    def test_calculate_sharpe(self) -> None:
        engine = BacktestEngine()
        returns = [0.01, 0.02, -0.005, 0.015, 0.008]

        result = engine._calculate_sharpe(returns)
        assert result is not None
        assert abs(result) > 0


class TestStrategyBacktestApi:
    def test_create_strategy_persists_flat_executable_config(
        self,
        client,
        db_session: Session,
    ) -> None:
        config = {
            "symbols": ["NABIL", "GBIME"],
            "entry_rules": [{"rule": "sma_cross_up"}],
            "exit_rules": [{"rule": "stop_loss", "params": {"stop": 0.05}}],
            "risk_rules": [{"rule": "max_position_size", "params": {"max_pct": 0.1}}],
        }
        response = client.post(
            "/strategies/",
            json={
                "name": "NEPSE trend starter",
                "version": "1.0.0",
                "description": "SMA20 above SMA50 trend template",
                "config": config,
            },
        )

        assert response.status_code == 200
        created = db_session.scalar(
            select(Strategy).where(Strategy.name == "NEPSE trend starter")
        )
        assert created is not None
        assert created.config["symbols"] == ["NABIL", "GBIME"]
        assert created.config["entry_rules"] == [{"rule": "sma_cross_up"}]
        assert created.config["description"] == "SMA20 above SMA50 trend template"
        assert "config" not in created.config

    def test_backtest_without_benchmark_sessions_returns_422_before_persisting(
        self,
        client,
        db_session: Session,
    ) -> None:
        from app.models.backtest import Backtest

        strategy = _seed_strategy(db_session)
        db_session.commit()

        response = client.post(
            "/strategies/backtests",
            json={
                "strategy_id": strategy.id,
                "config": {
                    "start_date": "2024-01-01",
                    "end_date": "2024-01-05",
                },
            },
        )

        assert response.status_code == 422
        assert "No NEPSE benchmark sessions found" in response.json()["detail"]
        assert db_session.scalars(select(Backtest)).all() == []

class TestBenchmarkService:
    def test_get_trading_dates_uses_observed_nepse_index_sessions(
        self, db_session: Session
    ) -> None:
        from app.models.benchmark import NEPSEIndex
        from app.services.backtest import BacktestEngine

        observed_dates = [
            date(2022, 6, 16),
            date(2022, 6, 17),
            date(2022, 6, 19),
            date(2022, 9, 15),
            date(2022, 9, 18),
            date(2024, 1, 1),
            date(2024, 1, 2),
            date(2024, 1, 3),
            date(2024, 1, 4),
            date(2026, 4, 10),
        ]
        db_session.add_all(
            NEPSEIndex(date=trading_date, close=2500.0)
            for trading_date in observed_dates
        )
        db_session.commit()

        engine = BacktestEngine()
        dates = engine._get_trading_dates("2022-06-16", "2022-06-19", ["TEST"])
        assert dates == [date(2022, 6, 16), date(2022, 6, 17), date(2022, 6, 19)]

        dates = engine._get_trading_dates("2024-01-01", "2024-01-05", ["TEST"])
        assert dates == [
            date(2024, 1, 1),
            date(2024, 1, 2),
            date(2024, 1, 3),
            date(2024, 1, 4),
        ]

        dates = engine._get_trading_dates("2026-04-10", "2026-04-12", ["TEST"])
        assert dates == [date(2026, 4, 10)]

    def test_get_trading_dates_fails_closed_without_benchmark_sessions(
        self, db_session: Session
    ) -> None:
        from app.services.backtest import BacktestEngine

        engine = BacktestEngine()
        dates = engine._get_trading_dates("2024-01-01", "2024-01-05", ["TEST"])
        assert dates == []


    def test_backtest_service_rejects_missing_benchmark_before_persisting(
        self, db_session: Session
    ) -> None:
        from app.models.backtest import Backtest
        from app.services.backtest import BacktestService

        strategy = _seed_strategy(db_session)
        service = BacktestService(session=db_session)
        try:
            service.run_backtest(
                strategy.id,
                {"start_date": "2024-01-01", "end_date": "2024-01-05"},
            )
        except ValueError as exc:
            assert "No NEPSE benchmark sessions found" in str(exc)
        else:
            raise AssertionError("Backtest should fail when no benchmark sessions exist")

        assert db_session.scalars(select(Backtest)).all() == []

    def test_calculate_benchmark_metrics(self) -> None:
        service = BenchmarkService()
        equity_curve = [
            {"equity": 100000, "cash": 0, "positions_value": 100000},
            {"equity": 105000, "cash": 0, "positions_value": 105000},
            {"equity": 110000, "cash": 0, "positions_value": 110000},
        ]

        result = service._calculate_benchmark_metrics(equity_curve)
        assert result["period_return"] == 0.1
        assert "annualized_return" in result

    def test_calculate_profit_factor(self) -> None:
        engine = BacktestEngine()
        trades = [
            {"action": "BUY", "symbol": "A", "quantity": 100, "price": 100.0},
            {"action": "SELL", "symbol": "A", "quantity": 100, "price": 120.0},
            {"action": "BUY", "symbol": "B", "quantity": 100, "price": 100.0},
            {"action": "SELL", "symbol": "B", "quantity": 100, "price": 80.0},
        ]
        result = engine._calculate_profit_factor(trades)
        assert result == 1.0

    def test_calculate_expectancy(self) -> None:
        engine = BacktestEngine()
        trades = [
            {"action": "BUY", "symbol": "A", "quantity": 100, "price": 100.0},
            {"action": "SELL", "symbol": "A", "quantity": 100, "price": 105.0},
        ]
        result = engine._calculate_expectancy(trades)
        assert result == 500.0


class TestVectorizedBacktestEngine:
    """Tests for the vectorized backtest engine (Phase 10)."""

    def test_init(self) -> None:
        """VectorizedBacktestEngine initializes with same params as BacktestEngine."""
        vec_engine = VectorizedBacktestEngine(
            initial_capital=Decimal("500000"),
            commission_rate=Decimal("0.01"),
            slippage_bps=Decimal("10"),
        )

        assert vec_engine.initial_capital == Decimal("500000")
        assert vec_engine.commission_rate == Decimal("0.01")
        assert vec_engine.slippage_bps == Decimal("10")

    def test_import_polars(self) -> None:
        """Polars is available for vectorized operations."""
        assert pl is not None
        # Basic Polars operation test
        df = pl.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        assert df.shape == (3, 2)


class TestModelsIntegration:
    def test_trade_model_has_transaction_fields(self) -> None:
        trade = Trade()
        assert hasattr(trade, "transaction_cost")
        assert hasattr(trade, "fill_rate")

    def test_benchmark_models_exist(self) -> None:
        assert NEPSEIndex is not None
        assert SectorIndex is not None
        assert hasattr(NEPSEIndex, "close")
        assert hasattr(SectorIndex, "sector")



class TestBacktestExecutionConfig:
    def test_service_applies_requested_execution_assumptions(self, db_session: Session) -> None:
        engine = BacktestService(session=db_session)._get_engine(
            db_session,
            {
                "initial_capital": 250000,
                "commission_rate": 0.003,
                "slippage_bps": 12,
                "execution_delay_bars": 2,
            },
        )

        assert engine.initial_capital == Decimal("250000")
        assert engine.commission_rate == Decimal("0.003")
        assert engine.slippage_bps == Decimal("12")
        assert engine.execution_delay_bars == 2
