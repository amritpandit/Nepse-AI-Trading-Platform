from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from app.api.routes import research as research_routes
from app.models.automation_run import AutomationRun
from app.models.data_source import IngestionLog
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
    older_stock = Stock(
        symbol="OLD",
        name="Older ordinary share",
        sector="Commercial Banks",
        instrument_type="ordinary",
        is_active=True,
    )
    db_session.add_all([stock, older_stock])
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
            Price(
                stock_id=older_stock.id,
                date=date(2026, 11, 23),
                open=90,
                high=95,
                low=88,
                close=94,
                volume=700,
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
            summary={
                "eligible_stocks": 1,
                "stocks_analyzed": 1,
                "scan_coverage_pct": 100.0,
                "snapshot_coverage_pct": 100.0,
                "analysis_failure_count": 0,
            },
        )
    )
    db_session.commit()

    result = research_routes.get_automation_overview(db=db_session)

    assert result["market_data"]["active_ordinary_shares"] == 2
    assert result["market_data"]["shares_with_price_history"] == 2
    assert result["market_data"]["coverage_pct"] == 100.0
    assert result["market_data"]["shares_with_latest_session"] == 1
    assert result["market_data"]["shares_not_at_latest_session"] == 1
    assert result["market_data"]["latest_session_coverage_pct"] == 50.0
    assert result["market_data"]["latest_session_gaps_returned"] == 1
    assert result["market_data"]["latest_session_gaps"][0]["symbol"] == "OLD"
    assert result["market_data"]["latest_session_gaps"][0]["last_price_date"] == "2026-11-23"
    assert result["market_data"]["price_rows"] == 3
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
    assert result["automation"]["latest_run"]["eligible_stocks"] == 1
    assert result["automation"]["latest_run"]["stocks_analyzed"] == 1
    assert result["automation"]["latest_run"]["scan_coverage_pct"] == 100.0
    assert result["automation"]["latest_run"]["snapshot_coverage_pct"] == 100.0
    assert result["automation"]["latest_run"]["analysis_failure_count"] == 0

def test_source_catalogue_diagnostics_reconciles_source_and_database(
    db_session: Session,
) -> None:
    current = Stock(
        symbol="AAA",
        name="Current Company",
        sector="Hydropower",
        instrument_type="ordinary",
        is_active=True,
    )
    source_stale = Stock(
        symbol="BBB",
        name="Source-Stale Company",
        sector="Finance",
        instrument_type="ordinary",
        is_active=True,
    )
    db_session.add_all([current, source_stale])
    db_session.flush()
    db_session.add_all(
        [
            Price(
                stock_id=current.id,
                date=date(2026, 11, 27),
                open=100,
                high=105,
                low=98,
                close=103,
                volume=1000,
            ),
            Price(
                stock_id=source_stale.id,
                date=date(2026, 11, 23),
                open=90,
                high=95,
                low=88,
                close=94,
                volume=700,
            ),
        ]
    )
    run = AutomationRun(
        job_type="market_data_import",
        status="success",
        run_key="source-audit-test",
        started_at=datetime(2026, 11, 28, tzinfo=UTC),
        completed_at=datetime(2026, 11, 28, tzinfo=UTC),
        summary={"source_latest_index_date": "2026-11-27"},
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        IngestionLog(
            source="nepal-market-data",
            status="success",
            records_fetched=3,
            records_inserted=1,
            records_rejected=0,
            started_at=datetime(2026, 11, 28, tzinfo=UTC),
            completed_at=datetime(2026, 11, 28, tzinfo=UTC),
            errors=json.dumps(
                {
                    "audit_version": 2,
                    "automation_run_id": run.id,
                    "catalogue_count": 3,
                    "catalogue_symbols": ["AAA", "BBB", "MISS"],
                    "processed_symbols": ["AAA", "BBB", "MISS"],
                    "full_history_backfill_symbols": ["MISS"],
                    "source_latest_dates": {
                        "AAA": "2026-11-27",
                        "BBB": "2026-11-23",
                        "MISS": "2026-11-27",
                    },
                    "download_errors": [],
                }
            ),
        )
    )
    db_session.commit()

    result = research_routes._source_catalogue_diagnostics(db_session)

    assert result["available"] is True
    assert result["catalogue_ordinary_shares"] == 3
    assert result["database_active_ordinary_shares"] == 2
    assert result["catalogue_only_symbols"] == ["MISS"]
    assert result["full_history_backfill_count"] == 1
    assert result["full_history_backfill_symbols"] == ["MISS"]
    assert result["status_counts"]["current"] == 1
    assert result["status_counts"]["source_has_no_row_for_latest_market_session"] == 1
    assert result["status_counts"]["no_database_history"] == 1
    assert result["latest_market_session_date"] == "2026-11-27"
    assert result["import_complete"] is False

def test_automation_scan_marks_partial_universe_as_failed(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def partial_scan(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "eligible_stocks": 3,
            "stocks_analyzed": 2,
            "scan_coverage_pct": 66.67,
            "analysis_failures": [
                {"symbol": "MISSING", "reason": "Insufficient valid OHLC history"}
            ],
            "snapshots_saved": 2,
            "signal_counts": {"HOLD": 2},
            "outcome_refresh": {},
            "model_version": "sector-stock-score-v1",
        }

    monkeypatch.setattr(
        research_routes,
        "refresh_and_save_stock_signals",
        partial_scan,
    )

    with pytest.raises(HTTPException) as exc_info:
        research_routes.automation_run_market_scan(
            db=db_session,
            _authorized=None,
            run_id="partial-universe-test",
        )

    assert exc_info.value.status_code == 502
    run = db_session.scalar(
        sa.select(AutomationRun).where(
            AutomationRun.job_type == "signal_scan",
            AutomationRun.run_key == "partial-universe-test",
        )
    )
    assert run is not None
    assert run.status == "failed"
    assert run.error_category == "PartialScan"
    assert run.records_processed == 2
    summary = run.summary
    assert summary is not None
    assert summary["eligible_stocks"] == 3
    assert summary["stocks_analyzed"] == 2
    assert summary["scan_coverage_pct"] == 66.67
    assert summary["snapshot_coverage_pct"] == 66.67
    assert summary["analysis_failure_count"] == 1
    assert summary["analysis_failures"][0]["symbol"] == "MISSING"


def test_automation_scan_marks_full_universe_as_success(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def complete_scan(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "eligible_stocks": 2,
            "stocks_analyzed": 2,
            "scan_coverage_pct": 100.0,
            "analysis_failures": [],
            "snapshots_saved": 2,
            "signal_counts": {"HOLD": 2},
            "outcome_refresh": {},
            "model_version": "sector-stock-score-v1",
        }

    monkeypatch.setattr(
        research_routes,
        "refresh_and_save_stock_signals",
        complete_scan,
    )

    result = research_routes.automation_run_market_scan(
        db=db_session,
        _authorized=None,
        run_id="complete-universe-test",
    )

    assert result["scan_complete"] is True
    assert result["scan_coverage_pct"] == 100.0
    assert result["snapshot_coverage_pct"] == 100.0
    assert result["eligible_stocks"] == 2
    assert result["stocks_analyzed"] == 2
    run = db_session.scalar(
        sa.select(AutomationRun).where(
            AutomationRun.job_type == "signal_scan",
            AutomationRun.run_key == "complete-universe-test",
        )
    )
    assert run is not None
    assert run.status == "success"
    assert run.error_category is None

