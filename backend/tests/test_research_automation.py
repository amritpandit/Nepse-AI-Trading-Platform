from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException

from app.api.routes import research as research_routes
from app.models.automation_run import AutomationRun
from app.models.price import Price
from app.models.signal import Signal
from app.models.stock import Stock
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

def test_github_actions_oidc_accepts_only_the_configured_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSigningKey:
        key = "test-public-key"

    claims = {
        "repository": research_routes.GITHUB_ACTIONS_REPOSITORY,
        "repository_id": research_routes.GITHUB_ACTIONS_REPOSITORY_ID,
        "repository_owner_id": research_routes.GITHUB_ACTIONS_OWNER_ID,
        "workflow_ref": research_routes.GITHUB_ACTIONS_WORKFLOW_REF,
        "ref": "refs/heads/main",
        "event_name": "schedule",
    }

    monkeypatch.setattr(
        research_routes._github_actions_jwks,
        "get_signing_key_from_jwt",
        lambda token: FakeSigningKey(),
    )
    monkeypatch.setattr(research_routes.jwt, "decode", lambda *args, **kwargs: claims)
    monkeypatch.setattr(research_routes.settings, "signal_automation_token", "")

    research_routes._require_signal_automation_token(
        token=None,
        authorization="Bearer test-github-oidc-token",
    )


def test_github_actions_oidc_rejects_another_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSigningKey:
        key = "test-public-key"

    claims = {
        "repository": "attacker/other-repository",
        "repository_id": research_routes.GITHUB_ACTIONS_REPOSITORY_ID,
        "repository_owner_id": research_routes.GITHUB_ACTIONS_OWNER_ID,
        "workflow_ref": research_routes.GITHUB_ACTIONS_WORKFLOW_REF,
        "ref": "refs/heads/main",
        "event_name": "schedule",
    }

    monkeypatch.setattr(
        research_routes._github_actions_jwks,
        "get_signing_key_from_jwt",
        lambda token: FakeSigningKey(),
    )
    monkeypatch.setattr(research_routes.jwt, "decode", lambda *args, **kwargs: claims)
    assert not research_routes._verify_github_actions_oidc_token("test-github-oidc-token")


def test_automation_overview_reports_archive_coverage_and_forward_progress(
    db_session: Session,
) -> None:
    stock = Stock(
        symbol="TEST",
        name="Test ordinary share",
        sector="Commercial Banks",
        instrument_type="ordinary",
        is_active=True,
    )
    db_session.add(stock)
    db_session.flush()
    db_session.add_all(
        [
            Price(
                stock_id=stock.id,
                date=date(2026, 11, 23),
                open=100,
                high=105,
                low=98,
                close=103,
                volume=1000,
            ),
            Price(
                stock_id=stock.id,
                date=date(2026, 11, 24),
                open=103,
                high=108,
                low=101,
                close=106,
                volume=1200,
            ),
        ]
    )
    db_session.add(
        Signal(
            stock_id=stock.id,
            date=date(2026, 11, 24),
            signal_type="BUY_TRIGGER",
            value=85,
            confidence=0.8,
            provider="research_score",
            model_source="sector-stock-score-v1",
            rule_params=json.dumps(
                {
                    "forward_performance": {
                        "outcomes": {
                            "1": {"status": "complete", "gross_return_pct": 2.0},
                            "5": {"status": "pending"},
                            "10": {"status": "review_required"},
                        }
                    }
                }
            ),
        )
    )
    db_session.add(
        AutomationRun(
            job_type="signal_scan",
            status="success",
            run_key="workflow-overview-test",
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            records_processed=1,
            summary={"stocks_analyzed": 1},
        )
    )
    db_session.commit()

    result = research_routes.get_automation_overview(db=db_session)

    assert result["market_data"]["active_ordinary_shares"] == 1
    assert result["market_data"]["shares_with_price_history"] == 1
    assert result["market_data"]["coverage_pct"] == 100.0
    assert result["market_data"]["price_rows"] == 2
    assert result["market_data"]["latest_price_date"] == "2026-11-24"
    assert result["signals"]["snapshot_count"] == 1
    assert result["signals"]["buy_trigger_count"] == 1
    assert result["forward_testing"]["horizons"]["1"]["buy"]["complete"] == 1
    assert result["forward_testing"]["horizons"]["5"]["buy"]["pending"] == 1
    assert (
        result["forward_testing"]["horizons"]["10"]["buy"]["review_required"] == 1
    )
    assert result["automation"]["run_count"] == 1
    assert result["automation"]["status_counts"]["success"] == 1

