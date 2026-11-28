"""Tests for universe-level chronological score-model validation."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.benchmark import NEPSEIndex
from app.models.price import Price
from app.models.stock import Stock


def _seed_validation_history(db_session: Session) -> None:
    stocks = [
        Stock(
            symbol="AAA",
            name="AAA Commercial Bank",
            sector="Commercial Bank",
            instrument_type="ordinary",
            is_active=True,
        ),
        Stock(
            symbol="BBB",
            name="BBB Commercial Bank",
            sector="Commercial Bank",
            instrument_type="ordinary",
            is_active=True,
        ),
        Stock(
            symbol="INACTIVE",
            name="Inactive Commercial Bank",
            sector="Commercial Bank",
            instrument_type="ordinary",
            is_active=False,
        ),
    ]
    db_session.add_all(stocks)
    db_session.flush()

    start = date(2024, 1, 1)
    for index in range(320):
        day = start + timedelta(days=index)
        market_close = Decimal(str(2000.0 + index * 0.2))
        db_session.add(
            NEPSEIndex(
                date=day,
                open=market_close - Decimal("0.1"),
                high=market_close + Decimal("0.3"),
                low=market_close - Decimal("0.3"),
                close=market_close,
                volume=100000,
            )
        )
        for stock, base, slope, acceleration in (
            (stocks[0], 100.0, 0.18, 0.0010),
            (stocks[1], 80.0, 0.24, 0.0015),
        ):
            close = Decimal(str(base + index * slope + index * index * acceleration))
            db_session.add(
                Price(
                    stock_id=stock.id,
                    date=day,
                    open=close - Decimal("0.1"),
                    high=close + Decimal("0.3"),
                    low=close - Decimal("0.3"),
                    close=close,
                    volume=1000 + index * 5,
                )
            )
    db_session.commit()


def test_universe_validation_returns_chronological_holdout_results(
    client: TestClient,
    db_session: Session,
) -> None:
    _seed_validation_history(db_session)

    response = client.get(
        "/research/validation/universe",
        params={
            "sector": "Commercial Bank",
            "limit": 1,
            "min_samples": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_version"] == "sector-stock-score-v1"
    assert payload["sector_filter"] == "Commercial Bank"
    assert payload["eligible_symbols_in_cohort"] == 2
    assert payload["symbols_analyzed"] == 1
    assert payload["minimum_buy_samples_for_ranking"] == 1
    assert len(payload["results"]) == 1

    result = payload["results"][0]
    assert result["symbol"] == "AAA"
    assert result["validation_status"] == "evaluated"
    assert result["test_start_date"] is not None
    assert result["test_end_date"] is not None
    assert result["clean_history_bars"] == 320
    assert set(result["horizons"]) == {"5", "10", "20"}
    assert isinstance(result["horizons"]["20"]["buy_samples"], int)
    assert result["rank_eligible"] is (
        result["horizons"]["20"]["buy_samples"] >= 1
        and result["horizons"]["20"]["nepse_benchmark_samples"] >= 1
        and result["horizons"]["20"]["mean_excess_vs_nepse_pct"] is not None
    )
    assert payload["methodology"]["portfolio_simulation"] is False
    assert payload["methodology"]["corporate_action_adjusted"] is False

    next_batch = client.get(
        "/research/validation/universe",
        params={
            "sector": "Commercial Bank",
            "limit": 1,
            "offset": 1,
            "min_samples": 1,
        },
    )
    assert next_batch.status_code == 200
    assert next_batch.json()["results"][0]["symbol"] == "BBB"


def test_universe_validation_rejects_invalid_limits(client: TestClient) -> None:
    response = client.get(
        "/research/validation/universe",
        params={"limit": 101},
    )
    assert response.status_code == 422

    invalid_offset = client.get(
        "/research/validation/universe",
        params={"offset": 501},
    )
    assert invalid_offset.status_code == 422
