"""Effective-dated transaction costs for NEPSE listed ordinary-share trades.

Rates are applied to the full executed transaction notional (not marginal slices).
The model intentionally excludes capital-gains tax: tax depends on taxpayer status,
holding-period rules, share-lot/WACC treatment and the effective date of tax notices.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


CENT = Decimal("0.01")
SEBON_SHARE_FEE_RATE = Decimal("0.00015")
CURRENT_DP_TRANSFER_FEE = Decimal("25.00")


@dataclass(frozen=True)
class CommissionSchedule:
    version: str
    effective_from: date
    rates: tuple[tuple[Decimal, Decimal], ...]
    minimum_commission: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class TransactionCosts:
    transaction_notional: Decimal
    commission_rate: Decimal
    broker_commission: Decimal
    sebon_fee: Decimal
    dp_charge: Decimal
    total: Decimal
    model_version: str


SCHEDULES: tuple[CommissionSchedule, ...] = (
    CommissionSchedule(
        version="nepse_commission_2011",
        effective_from=date(2011, 4, 25),
        rates=(
            (Decimal("50000"), Decimal("0.0100")),
            (Decimal("500000"), Decimal("0.0090")),
            (Decimal("1000000"), Decimal("0.0080")),
            (Decimal("Infinity"), Decimal("0.0070")),
        ),
        minimum_commission=Decimal("25.00"),
    ),
    CommissionSchedule(
        version="nepse_commission_2016",
        effective_from=date(2016, 7, 21),
        rates=(
            (Decimal("50000"), Decimal("0.0060")),
            (Decimal("500000"), Decimal("0.0055")),
            (Decimal("2000000"), Decimal("0.0050")),
            (Decimal("10000000"), Decimal("0.0045")),
            (Decimal("Infinity"), Decimal("0.0040")),
        ),
        minimum_commission=Decimal("25.00"),
    ),
    CommissionSchedule(
        version="nepse_commission_2020",
        effective_from=date(2020, 12, 27),
        rates=(
            (Decimal("50000"), Decimal("0.0040")),
            (Decimal("500000"), Decimal("0.0037")),
            (Decimal("2000000"), Decimal("0.0034")),
            (Decimal("10000000"), Decimal("0.0030")),
            (Decimal("Infinity"), Decimal("0.0027")),
        ),
        minimum_commission=Decimal("10.00"),
    ),
    CommissionSchedule(
        version="nepse_commission_2024",
        effective_from=date(2024, 5, 14),
        rates=(
            (Decimal("50000"), Decimal("0.0036")),
            (Decimal("500000"), Decimal("0.0033")),
            (Decimal("2000000"), Decimal("0.0031")),
            (Decimal("10000000"), Decimal("0.0027")),
            (Decimal("Infinity"), Decimal("0.0024")),
        ),
        minimum_commission=Decimal("10.00"),
    ),
)


class NepalEquityTransactionCostModel:
    """Broker commission, SEBON fee and depository transfer costs for NEPSE equities."""

    version = "nepse_equity_costs_v1"
    sebon_fee_rate = SEBON_SHARE_FEE_RATE
    dp_fee_effective_date = date(2013, 4, 4)
    dp_fixed_fee = CURRENT_DP_TRANSFER_FEE

    @staticmethod
    def _money(value: Decimal) -> Decimal:
        return value.quantize(CENT, rounding=ROUND_HALF_UP)

    @classmethod
    def schedule_for(cls, trade_date: date) -> CommissionSchedule:
        applicable = [item for item in SCHEDULES if item.effective_from <= trade_date]
        if not applicable:
            raise ValueError(
                f"No verified NEPSE equity commission schedule is configured for {trade_date.isoformat()}. "
                "Do not report net backtest returns for this period until its fee schedule is verified."
            )
        return max(applicable, key=lambda item: item.effective_from)

    @classmethod
    def commission_rate_for(cls, notional: Decimal, trade_date: date) -> tuple[Decimal, CommissionSchedule]:
        if notional <= 0:
            raise ValueError("Transaction notional must be greater than zero.")
        schedule = cls.schedule_for(trade_date)
        for upper_bound, rate in schedule.rates:
            if notional <= upper_bound:
                return rate, schedule
        raise ValueError("No commission tier matched the transaction notional.")

    @classmethod
    def calculate(
        cls,
        notional: Decimal,
        trade_date: date,
        action: str,
    ) -> TransactionCosts:
        """Return modeled costs for one executed BUY or SELL transfer.

        Broker commission and SEBON share transaction fee apply to both sides.
        The DP charge applies only to SELL transfers: NPR 25 from 4 April 2013,
        and the prior NPR 20 or 0.25% (whichever higher) convention before then.
        The backtest aggregates fills by symbol/session, so one DP fee is charged
        for each executed symbol/day sale.
        """
        normalized_action = action.strip().upper()
        if normalized_action not in {"BUY", "SELL"}:
            raise ValueError("action must be BUY or SELL")
        if notional <= 0:
            raise ValueError("Transaction notional must be greater than zero.")

        commission_rate, schedule = cls.commission_rate_for(notional, trade_date)
        broker_commission = notional * commission_rate
        if schedule.minimum_commission > 0:
            broker_commission = max(broker_commission, schedule.minimum_commission)
        broker_commission = cls._money(broker_commission)

        sebon_fee = cls._money(notional * cls.sebon_fee_rate)

        if normalized_action == "SELL":
            if trade_date < cls.dp_fee_effective_date:
                dp_charge = cls._money(max(Decimal("20.00"), notional * Decimal("0.0025")))
            else:
                dp_charge = cls.dp_fixed_fee
        else:
            dp_charge = Decimal("0.00")

        total = broker_commission + sebon_fee + dp_charge
        return TransactionCosts(
            transaction_notional=cls._money(notional),
            commission_rate=commission_rate,
            broker_commission=broker_commission,
            sebon_fee=sebon_fee,
            dp_charge=dp_charge,
            total=cls._money(total),
            model_version=f"{cls.version}:{schedule.version}",
        )
