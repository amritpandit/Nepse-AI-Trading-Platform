from __future__ import annotations

import json
from datetime import date

import httpx
from app.models.data_source import DataSource, IngestionLog
from app.models.price import Price
from app.models.stock import Stock
from app.services.nepal_market_data import NepalMarketDataImporter
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_equity_import_records_date_level_rejection_evidence(db_session: Session) -> None:
    catalogue = (
        "symbol,name,sector,status,instrument_type\n"
        "NABIL,Nabil Bank,Banking,listed,ordinary\n"
    )
    prices = (
        "published_date,open,high,low,close,traded_quantity\n"
        "2026-06-01,99,101,98,100,1000\n"
        "2026-06-02,100,99,98,101,1000\n"
        "not-a-date,100,101,98,100,1000\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/data/reference/nepse-symbols.csv"):
            return httpx.Response(200, text=catalogue, request=request)
        if request.url.path.endswith("/data/nepse/NABIL.csv"):
            return httpx.Response(200, text=prices, request=request)
        return httpx.Response(404, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    importer = NepalMarketDataImporter(
        session=db_session,
        client=client,
        base_url="https://market-data.test",
    )
    try:
        result = importer.sync_equities(
            offset=0,
            batch_size=1,
            start_date="2026-06-02",
            dry_run=False,
            automation_run_id=42,
        )
    finally:
        importer.close()
        client.close()

    assert result["symbols_succeeded"] == 1
    assert result["rows_fetched"] == 2
    assert result["rows_inserted"] == 1
    assert result["rows_rejected"] == 2
    assert result["full_history_backfill_symbols"] == ["NABIL"]

    price_rows = db_session.scalars(select(Price)).all()
    assert len(price_rows) == 1
    assert price_rows[0].date.isoformat() == "2026-06-01"

    log = db_session.scalar(
        select(IngestionLog).where(IngestionLog.source == "nepal-market-data")
    )
    assert log is not None
    assert log.status == "success"
    assert log.source_id is not None
    source = db_session.get(DataSource, log.source_id)
    assert source is not None
    assert source.name == "nepal-market-data"
    assert source.type == "csv"
    payload = json.loads(log.errors or "{}")
    assert payload["audit_version"] == 3
    assert payload["automation_run_id"] == 42
    assert payload["catalogue_count"] == 1
    assert payload["catalogue_symbols"] == ["NABIL"]
    assert payload["full_history_backfill_symbols"] == ["NABIL"]
    assert payload["source_latest_dates"] == {"NABIL": "2026-06-02"}
    assert payload["rejected_by_symbol_date"] == {"NABIL": {"2026-06-02": 1}}
    assert payload["invalid_date_rows_by_symbol"] == {"NABIL": 1}
    assert result["catalogue_symbols_count"] == 1
    assert result["catalogue_symbols"] == ["NABIL"]
    assert result["source_latest_dates"] == {"NABIL": "2026-06-02"}

def test_full_history_backfill_only_applies_to_stocks_without_price_rows(
    db_session: Session,
) -> None:
    catalogue = (
        "symbol,name,sector,status,instrument_type\n"
        "EMPTY,Empty History Share,Hydropower,listed,ordinary\n"
        "HIST,Existing History Share,Finance,listed,ordinary\n"
    )
    empty_prices = (
        "published_date,open,high,low,close,traded_quantity\n"
        "2024-01-01,99,101,98,100,1000\n"
        "2026-06-02,100,103,99,102,1200\n"
    )
    historical_prices = (
        "published_date,open,high,low,close,traded_quantity\n"
        "2024-01-01,49,51,48,50,500\n"
        "2026-06-02,50,53,49,52,700\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/data/reference/nepse-symbols.csv"):
            return httpx.Response(200, text=catalogue, request=request)
        if request.url.path.endswith("/data/nepse/EMPTY.csv"):
            return httpx.Response(200, text=empty_prices, request=request)
        if request.url.path.endswith("/data/nepse/HIST.csv"):
            return httpx.Response(200, text=historical_prices, request=request)
        return httpx.Response(404, request=request)

    empty = Stock(
        symbol="EMPTY",
        name="Empty History Share",
        sector="Hydropower",
        instrument_type="ordinary",
        is_active=True,
    )
    historical = Stock(
        symbol="HIST",
        name="Existing History Share",
        sector="Finance",
        instrument_type="ordinary",
        is_active=True,
    )
    db_session.add_all([empty, historical])
    db_session.flush()
    db_session.add(
        Price(
            stock_id=historical.id,
            date=date(2024, 1, 1),
            open=49,
            high=51,
            low=48,
            close=50,
            volume=500,
        )
    )
    db_session.commit()

    client = httpx.Client(transport=httpx.MockTransport(handler))
    importer = NepalMarketDataImporter(
        session=db_session,
        client=client,
        base_url="https://market-data.test",
    )
    try:
        result = importer.sync_equities(
            offset=0,
            batch_size=10,
            start_date="2026-06-02",
            automation_run_id=99,
        )
    finally:
        importer.close()
        client.close()

    assert result["symbols_succeeded"] == 2
    assert result["full_history_backfill_symbols"] == ["EMPTY"]
    assert result["rows_fetched"] == 3
    assert result["rows_inserted"] == 3

    dates_by_symbol = {
        symbol: [
            row.date.isoformat()
            for row in db_session.scalars(
                select(Price)
                .where(Price.stock_id == stock_id)
                .order_by(Price.date)
            ).all()
        ]
        for symbol, stock_id in (("EMPTY", empty.id), ("HIST", historical.id))
    }
    assert dates_by_symbol["EMPTY"] == ["2024-01-01", "2026-06-02"]
    assert dates_by_symbol["HIST"] == ["2024-01-01", "2026-06-02"]

