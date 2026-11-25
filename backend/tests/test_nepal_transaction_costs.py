from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from app.services.backtest import BacktestEngine
from app.services.nepal_transaction_costs import NepalEquityTransactionCostModel


@pytest.mark.parametrize(
    ("trade_date", "expected_rate"),
    [
        (date(2011, 4, 25), Decimal("0.0100")),
        (date(2016, 7, 21), Decimal("0.0060")),
        (date(2020, 12, 27), Decimal("0.0040")),
        (date(2024, 5, 14), Decimal("0.0036")),
    ],
)
def test_commission_schedule_uses_effective_date(
    trade_date: date,
    expected_rate: Decimal,
) -> None:
    rate, _schedule = NepalEquityTransactionCostModel.commission_rate_for(
        Decimal("40000"),
        trade_date,
    )
    assert rate == expected_rate


@pytest.mark.parametrize(
    ("trade_date", "expected_minimum"),
    [
        (date(2011, 4, 25), Decimal("25.00")),
        (date(2016, 7, 21), Decimal("25.00")),
        (date(2020, 12, 27), Decimal("10.00")),
        (date(2024, 5, 14), Decimal("10.00")),
    ],
)
def test_historical_minimum_commission_changes_by_schedule(
    trade_date: date,
    expected_minimum: Decimal,
) -> None:
    costs = NepalEquityTransactionCostModel.calculate(
        Decimal("1000"),
        trade_date,
        "BUY",
    )

    assert costs.broker_commission == expected_minimum



def test_current_fee_breakdown_for_npr_50000_trade() -> None:
    costs = NepalEquityTransactionCostModel.calculate(
        Decimal("50000"),
        date(2026, 11, 24),
        "SELL",
    )

    assert costs.commission_rate == Decimal("0.0036")
    assert costs.broker_commission == Decimal("180.00")
    assert costs.sebon_fee == Decimal("7.50")
    assert costs.dp_charge == Decimal("25.00")
    assert costs.total == Decimal("212.50")
    assert costs.model_version == "nepse_equity_costs_v1:nepse_commission_2024"


def test_current_minimum_broker_commission_applies_to_small_trade() -> None:
    costs = NepalEquityTransactionCostModel.calculate(
        Decimal("2000"),
        date(2026, 11, 24),
        "SELL",
    )

    assert costs.broker_commission == Decimal("10.00")
    assert costs.sebon_fee == Decimal("0.30")
    assert costs.dp_charge == Decimal("25.00")
    assert costs.total == Decimal("35.30")


def test_tier_rate_applies_to_full_transaction_not_marginal_band() -> None:
    costs = NepalEquityTransactionCostModel.calculate(
        Decimal("50001"),
        date(2026, 11, 24),
        "SELL",
    )

    assert costs.commission_rate == Decimal("0.0033")
    assert costs.broker_commission == Decimal("165.00")


def test_pre_2013_depository_fee_uses_historical_higher_of_rule() -> None:
    costs = NepalEquityTransactionCostModel.calculate(
        Decimal("10000"),
        date(2012, 1, 1),
        "SELL",
    )

    assert costs.broker_commission == Decimal("100.00")
    assert costs.sebon_fee == Decimal("1.50")
    assert costs.dp_charge == Decimal("25.00")
    assert costs.total == Decimal("126.50")


def test_dp_transfer_fee_is_charged_only_on_sell() -> None:
    buy = NepalEquityTransactionCostModel.calculate(
        Decimal("50000"),
        date(2026, 11, 24),
        "BUY",
    )
    sell = NepalEquityTransactionCostModel.calculate(
        Decimal("50000"),
        date(2026, 11, 24),
        "SELL",
    )

    assert buy.dp_charge == Decimal("0.00")
    assert buy.total == Decimal("187.50")
    assert sell.dp_charge == Decimal("25.00")
    assert sell.total == Decimal("212.50")


def test_invalid_transaction_action_is_rejected() -> None:
    with pytest.raises(ValueError, match="action must be BUY or SELL"):
        NepalEquityTransactionCostModel.calculate(
            Decimal("50000"),
            date(2026, 11, 24),
            "TRANSFER",
        )


def test_dates_before_verified_commission_history_are_rejected() -> None:
    with pytest.raises(ValueError, match="No verified NEPSE equity commission schedule"):
        NepalEquityTransactionCostModel.calculate(
            Decimal("10000"),
            date(2011, 4, 24),
            "SELL",
        )


def test_explicit_legacy_flat_commission_is_preserved() -> None:
    engine = BacktestEngine(commission_rate=Decimal("0.005"))

    costs = engine._transaction_cost_breakdown(Decimal("1000"), "2026-11-24", "BUY")

    assert costs["broker_commission"] == Decimal("5.000")
    assert costs["sebon_fee"] == Decimal("0")
    assert costs["dp_charge"] == Decimal("0")
    assert costs["transaction_cost"] == Decimal("5.000")
    assert costs["model_version"] == "legacy_flat_commission_v1"


def test_default_backtest_uses_effective_dated_nepse_cost_model() -> None:
    engine = BacktestEngine()

    assert engine.commission_rate is None
    assert engine.fee_model_version == "nepse_equity_costs_v1"


def test_buy_sizing_checks_across_non_monotonic_commission_threshold() -> None:
    engine = BacktestEngine()

    # At NPR 2,000,000, the 0.31% fee makes the 20,000-share fill
    # exceed available cash. Crossing into the next band drops the rate
    # to 0.27%, making 20,001 shares affordable again.
    quantity = engine._max_affordable_buy_quantity(
        desired_quantity=25000,
        execution_price=Decimal("100"),
        available_cash=Decimal("2006450"),
        trade_date="2026-11-24",
    )

    assert quantity == 20007
    notional = Decimal(quantity) * Decimal("100")
    fees = engine._transaction_cost_breakdown(
        notional,
        "2026-11-24",
        "BUY",
    )["transaction_cost"]
    assert notional + fees <= Decimal("2006450")

    next_notional = Decimal(quantity + 1) * Decimal("100")
    next_fees = engine._transaction_cost_breakdown(
        next_notional,
        "2026-11-24",
        "BUY",
    )["transaction_cost"]
    assert next_notional + next_fees > Decimal("2006450")


def test_buy_quantity_is_capped_to_cash_after_all_fees() -> None:
    engine = BacktestEngine()

    quantity = engine._max_affordable_buy_quantity(
        desired_quantity=500,
        execution_price=Decimal("100"),
        available_cash=Decimal("50100"),
        trade_date="2026-11-24",
    )

    assert quantity == 499
    notional = Decimal(quantity) * Decimal("100")
    fees = engine._transaction_cost_breakdown(notional, "2026-11-24", "BUY")["transaction_cost"]
    assert notional + fees <= Decimal("50100")
    next_notional = Decimal(quantity + 1) * Decimal("100")
    next_fees = engine._transaction_cost_breakdown(next_notional, "2026-11-24", "BUY")["transaction_cost"]
    assert next_notional + next_fees > Decimal("50100")
