from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import sqlalchemy as sa
from app.models.data_quality import DataTrust
from app.models.price import Price
from app.models.stock import Stock


def _seed_history(db_session, with_trust: bool) -> tuple[Stock, date]:
    stock = Stock(symbol="NABIL", name="Nabil Bank", sector="Banking", instrument_type="equity")
    db_session.add(stock)
    db_session.flush()

    start = date(2026, 1, 1)
    prices = []
    for index in range(80):
        close = 100.0 + (index * 0.45) + (1.2 * ((index % 9) / 9.0))
        open_price = close - 0.2
        prices.append(
            Price(
                stock_id=stock.id,
                date=start + timedelta(days=index),
                open=Decimal(str(open_price)),
                high=Decimal(str(close + 0.8)),
                low=Decimal(str(open_price - 0.8)),
                close=Decimal(str(close)),
                volume=1000 + (index % 7) * 120,
            )
        )
    db_session.add_all(prices)
    db_session.flush()
    latest_date = prices[-1].date

    if with_trust:
        db_session.add(
            DataTrust(
                stock_id=stock.id,
                symbol=stock.symbol,
                date=latest_date,
                trust_score=0.92,
                trust_version="test-v1",
            )
        )
    db_session.commit()
    return stock, latest_date


def test_technical_analysis_endpoint_is_read_only_without_trust_record(client, db_session) -> None:
    _seed_history(db_session, with_trust=False)

    response = client.get("/analysis/technical/NABIL?limit=90")

    assert response.status_code == 200
    payload = response.json()
    assert payload["observation_count"] == 80
    assert payload["signal"] == "HOLD"
    assert payload["signal_eligible"] is False
    assert payload["data_quality"]["passed"] is False
    assert db_session.scalar(sa.select(sa.func.count()).select_from(DataTrust)) == 0


def test_technical_analysis_endpoint_uses_existing_trust_record(client, db_session) -> None:
    _seed_history(db_session, with_trust=True)

    response = client.get("/analysis/technical/NABIL?limit=90")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_quality"]["passed"] is True
    assert payload["data_quality"]["trust_score"] == 0.92
    assert payload["signal_eligible"] is True
    assert db_session.scalar(sa.select(sa.func.count()).select_from(DataTrust)) == 1


def test_technical_analysis_endpoint_honours_as_of_date(client, db_session) -> None:
    _, latest_date = _seed_history(db_session, with_trust=True)
    cut_off = latest_date - timedelta(days=10)

    response = client.get(
        "/analysis/technical/NABIL",
        params={"as_of": cut_off.isoformat(), "limit": 90},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["as_of"] == cut_off.isoformat()
    assert payload["observation_count"] == 70
