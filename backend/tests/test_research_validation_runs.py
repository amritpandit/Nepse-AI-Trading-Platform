"""Tests for persisted universe score-validation run history."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes import research as research_routes
from app.models.research_validation_run import ResearchValidationRun


def _sample_result() -> dict:
    return {
        "model_version": "sector-stock-score-v1",
        "sector_filter": "Commercial Bank",
        "requested_limit": 10,
        "requested_offset": 20,
        "eligible_symbols_in_cohort": 42,
        "symbols_analyzed": 10,
        "rank_eligible_symbols": 3,
        "minimum_buy_samples_for_ranking": 5,
        "latest_price_date_in_cohort": "2026-11-24",
        "results": [
            {
                "symbol": "NABIL",
                "rank": 1,
                "rank_eligible": True,
                "horizons": {
                    "20": {
                        "buy_samples": 8,
                        "nepse_benchmark_samples": 8,
                        "mean_excess_vs_nepse_pct": 2.4,
                    }
                },
            }
        ],
        "cross_symbol_summary": {"20": {"symbols_with_minimum_samples": 3}},
        "methodology": {"portfolio_simulation": False},
    }


def test_validation_run_persists_result_and_is_retrievable(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = _sample_result()

    def fake_run(
        self,
        *,
        sector: str | None = None,
        limit: int = 30,
        offset: int = 0,
        min_samples: int = 5,
    ) -> dict:
        del self
        assert (sector, limit, offset, min_samples) == ("Commercial Bank", 10, 20, 5)
        return sample

    monkeypatch.setattr(research_routes.UniverseScoreValidationService, "run", fake_run)

    response = client.post(
        "/research/validation/universe/runs",
        params={
            "sector": "Commercial Bank",
            "limit": 10,
            "offset": 20,
            "min_samples": 5,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["run_id"] > 0
    assert payload["symbols_analyzed"] == 10
    assert payload["rank_eligible_symbols"] == 3
    assert payload["latest_price_date"] == "2026-11-24"
    assert payload["result"]["results"][0]["symbol"] == "NABIL"

    row = db_session.get(ResearchValidationRun, payload["run_id"])
    assert row is not None
    assert row.user_id == "test-user"
    assert row.parameters["offset"] == 20
    assert row.status == "success"
    assert row.result == sample
    assert row.completed_at is not None

    history = client.get("/research/validation/universe/runs")
    assert history.status_code == 200
    assert history.json()["count"] == 1
    assert history.json()["runs"][0]["run_id"] == payload["run_id"]

    detail = client.get(f"/research/validation/universe/runs/{payload['run_id']}")
    assert detail.status_code == 200
    assert detail.json()["result"]["methodology"]["portfolio_simulation"] is False


def test_validation_run_failure_is_audited(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_run(self, **kwargs) -> dict:
        del self, kwargs
        raise RuntimeError("synthetic validation failure")

    monkeypatch.setattr(research_routes.UniverseScoreValidationService, "run", fail_run)

    response = client.post("/research/validation/universe/runs")
    assert response.status_code == 500
    assert "saved run record" in response.json()["detail"]

    row = db_session.scalar(
        select(ResearchValidationRun).order_by(ResearchValidationRun.id.desc())
    )
    assert row is not None
    assert row.status == "failed"
    assert row.error_message == "synthetic validation failure"
    assert row.completed_at is not None


def test_validation_run_history_is_scoped_to_authenticated_user(
    client: TestClient,
    db_session: Session,
) -> None:
    from app.models.user import User

    other_user = User(
        id="other-test-user",
        email="other@example.com",
        hashed_password="x",
        role="researcher",
    )
    db_session.add(other_user)
    db_session.flush()
    row = ResearchValidationRun(
        user_id=other_user.id,
        model_version="sector-stock-score-v1",
        requested_limit=10,
        requested_offset=0,
        min_samples=5,
        status="success",
        parameters={"limit": 10, "offset": 0, "min_samples": 5},
        result=_sample_result(),
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    db_session.add(row)
    db_session.commit()

    history = client.get("/research/validation/universe/runs")
    assert history.status_code == 200
    assert history.json()["count"] == 0

    detail = client.get(f"/research/validation/universe/runs/{row.id}")
    assert detail.status_code == 404
