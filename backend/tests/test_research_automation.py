from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.api.routes import research as research_routes
from app.models.automation_run import AutomationRun
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
    research_routes._require_signal_automation_token(token="test-secret")


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
        return deepcopy(payload)

    monkeypatch.setattr(StockSectorResearchService, "scan_stocks", fake_scan)

    compact = research_routes.scan_stock_signals(db=db_session, limit=10)
    assert "price_history_for_chart" not in compact["signals"][0]

    detailed = research_routes.scan_stock_signals(
        db=db_session,
        limit=10,
        include_charts=True,
    )
    assert detailed["signals"][0]["price_history_for_chart"]

def test_automation_history_exposes_persisted_pipeline_runs_without_secrets(
    db_session: Session,
) -> None:
    db_session.add(
        AutomationRun(
            job_type="signal_scan",
            status="success",
            run_key="workflow-123",
            records_processed=240,
            records_inserted=12,
            records_updated=3,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            summary={"stocks_analyzed": 240, "signal_counts": {"BUY_TRIGGER": 4}},
        )
    )
    db_session.commit()

    result = research_routes.get_automation_history(db=db_session, limit=10)
    assert result["count"] == 1
    run = result["runs"][0]
    assert run["job_type"] == "signal_scan"
    assert run["status"] == "success"
    assert run["summary"]["stocks_analyzed"] == 240
    assert "token" not in run


def test_automation_run_is_recorded_before_work_and_finalized(db_session: Session) -> None:
    run_id = research_routes._start_automation_run(
        db_session,
        job_type="signal_scan",
        run_key="workflow-running",
    )
    running = db_session.get(AutomationRun, run_id)
    assert running is not None
    assert running.status == "running"
    assert running.completed_at is None

    completed = research_routes._finish_automation_run(
        db_session,
        run_id,
        status="success",
        records_processed=240,
        summary={"stocks_analyzed": 240},
    )
    assert completed.status == "success"
    assert completed.completed_at is not None
    assert completed.records_processed == 240
    assert completed.summary == {"stocks_analyzed": 240}
