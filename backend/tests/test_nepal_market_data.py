from __future__ import annotations

import json

import httpx
from app.models.data_source import DataSource, IngestionLog
from app.models.price import Price
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
        result = importer.sync_equities(offset=0, batch_size=1, dry_run=False)
    finally:
        importer.close()
        client.close()

    assert result["symbols_succeeded"] == 1
    assert result["rows_fetched"] == 2
    assert result["rows_inserted"] == 1
    assert result["rows_rejected"] == 2

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
    assert payload["audit_version"] == 1
    assert payload["rejected_by_symbol_date"] == {"NABIL": {"2026-06-02": 1}}
    assert payload["invalid_date_rows_by_symbol"] == {"NABIL": 1}
