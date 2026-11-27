from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routes import research as research_routes
from app.services.stock_sector_research import StockSectorResearchService
from sqlalchemy.orm import Session


def test_signal_automation_token_requires_server_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(research_routes.settings, "signal_automation_token", "")
    with pytest.raises(HTTPException) as exc_info:
        research_routes._require_signal_automation_token(token="anything")
    assert exc_info.value.status_code == 503


def test_signal_automation_token_rejects_wrong_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(research_routes.settings, "signal_automation_token", "test-secret")
    with pytest.raises(HTTPException) as exc_info:
        research_routes._require_signal_automation_token(token="wrong-secret")
    assert exc_info.value.status_code == 401


def test_signal_automation_token_accepts_matching_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(research_routes.settings, "signal_automation_token", "test-secret")
    assert research_routes._require_signal_automation_token(token="test-secret") is None


def test_scan_endpoint_omits_chart_history_by_default(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "stocks_analyzed": 1,
        "signals": [
            {
                "symbol": "TEST",
                "price_history_for_chart": [{"date": "2026-01-01", "close": 100}],
            }
        ],
    }
    def fake_scan(
        self: StockSectorResearchService,
        limit: int,
    ) -> dict[str, object]:
        del self, limit
        return payload

    monkeypatch.setattr(StockSectorResearchService, "scan_stocks", fake_scan)

    compact = research_routes.scan_stock_signals(db=db_session, limit=10)
    assert "price_history_for_chart" not in compact["signals"][0]

    detailed = research_routes.scan_stock_signals(
        db=db_session,
        limit=10,
        include_charts=True,
    )
    assert detailed["signals"][0]["price_history_for_chart"]
