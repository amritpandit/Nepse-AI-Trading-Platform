"""Model governance: approval workflow before production-ready marking.

Phase 11. The ML promotion gate (``ml/training.py``) decides whether a model is
statistically *promotable*. Governance adds a human-in-the-loop layer on top:
a trained/promoted model must be explicitly reviewed and approved before it can
be marked production-ready. State is persisted on the ``ModelRegistry`` row's
``params`` JSON so no schema migration is required.

Lifecycle::

    trained --submit--> pending_approval --approve--> approved --promote--> production
                              |                            |
                              +----------- reject ---------+--> rejected
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import sqlalchemy as sa
from app.models.model_registry import ModelRegistry
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Governance states
GOV_TRAINED = "trained"
GOV_PENDING = "pending_approval"
GOV_APPROVED = "approved"
GOV_REJECTED = "rejected"
GOV_PRODUCTION = "production"

_VALID_STATES = {GOV_TRAINED, GOV_PENDING, GOV_APPROVED, GOV_REJECTED, GOV_PRODUCTION}


class GovernanceError(Exception):
    """Raised when an invalid governance transition is requested."""


class ModelGovernanceService:
    """Manage human approval of models stored in the model registry."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --------------------------------------------------------------- resolving
    def _resolve(self, model_ref: str | int) -> ModelRegistry:
        """Find a registry row by primary id, version, or embedded model_id."""
        ref = str(model_ref)
        query = self._session.query(ModelRegistry)
        entry: ModelRegistry | None = None

        if ref.isdigit():
            entry = query.filter(ModelRegistry.id == int(ref)).first()
        if entry is None:
            entry = query.filter(
                sa.or_(
                    ModelRegistry.version == ref,
                    ModelRegistry.version == f"v{ref}",
                    ModelRegistry.version.contains(ref),
                )
            ).first()
        if entry is None:
            raise GovernanceError(f"Model not found: {model_ref}")
        return entry

    def _governance(self, entry: ModelRegistry) -> dict[str, Any]:
        params = dict(entry.params or {})
        return dict(params.get("governance") or {})

    def _set_governance(self, entry: ModelRegistry, governance: dict[str, Any]) -> None:
        # JSON columns do not track in-place mutation; reassign a fresh dict so
        # SQLAlchemy flushes the change.
        params = dict(entry.params or {})
        params["governance"] = governance
        entry.params = params

    # --------------------------------------------------------------- workflow
    def submit_for_approval(self, model_ref: str | int, notes: str | None = None) -> dict[str, Any]:
        entry = self._resolve(model_ref)
        gov = self._governance(entry)
        current = gov.get("status", GOV_TRAINED)
        if current in (GOV_PENDING, GOV_APPROVED, GOV_PRODUCTION):
            raise GovernanceError(f"Model already in '{current}' state")

        # A manual reviewer can review only models that passed the current
        # quantitative gate. This also blocks older registry entries that were
        # promoted under the former, weaker 55% win-rate threshold.
        params = dict(entry.params or {})
        model_metrics = dict(entry.metrics or {})
        status = params.get("status")
        win_rate_value = model_metrics.get("win_rate", 0.0)
        if isinstance(win_rate_value, dict):
            win_rate = float(win_rate_value.get("mean", 0.0))
        else:
            win_rate = float(win_rate_value or 0.0)
        signal_count = float(
            model_metrics.get(
                "signal_count_total",
                model_metrics.get("signal_count", 0.0),
            )
            or 0.0
        )
        net_expectancy_value = model_metrics.get("net_expectancy", 0.0)
        if isinstance(net_expectancy_value, dict):
            net_expectancy = float(net_expectancy_value.get("mean", 0.0))
        else:
            net_expectancy = float(net_expectancy_value or 0.0)
        drawdown_value = model_metrics.get("max_drawdown", 1.0)
        if isinstance(drawdown_value, dict):
            drawdown = float(drawdown_value.get("max", 1.0))
        else:
            drawdown = float(drawdown_value) if drawdown_value is not None else 1.0
        sharpe_value = model_metrics.get("sharpe_ratio", 0.0)
        if isinstance(sharpe_value, dict):
            sharpe = float(sharpe_value.get("mean", 0.0))
        else:
            sharpe = float(sharpe_value or 0.0)
        is_walk_forward = "n_folds" in params or "signal_count_total" in model_metrics
        min_fold_win_rate = float(model_metrics.get("min_fold_win_rate", 0.0) or 0.0)
        n_folds = float(params.get("n_folds", model_metrics.get("n_folds", 0.0)) or 0.0)
        required_signals = 100 if is_walk_forward else 50
        required_sharpe = 1.0 if is_walk_forward else 0.5

        if status != "promoted":
            raise GovernanceError("Model must pass the quantitative promotion gate first")
        if win_rate <= 0.70 or signal_count < required_signals or net_expectancy <= 0:
            raise GovernanceError(
                "Model must have >70% net out-of-sample signal wins, sufficient test signals, "
                "and positive net expectancy"
            )
        if drawdown >= 0.20 or sharpe <= required_sharpe:
            raise GovernanceError("Model does not meet the risk-adjusted return requirements")
        if is_walk_forward and (
            n_folds < 3 or min_fold_win_rate < 0.60
        ):
            raise GovernanceError("Walk-forward results are not stable across enough folds")

        gov.update(
            {
                "status": GOV_PENDING,
                "production_ready": False,
                "submitted_at": dt.datetime.now(dt.UTC).isoformat(),
                "submit_notes": notes,
            }
        )
        self._set_governance(entry, gov)
        self._session.commit()
        logger.info("Model %s submitted for approval", entry.version)
        return self.get_status(model_ref)

    def approve(
        self, model_ref: str | int, reviewer: str, notes: str | None = None
    ) -> dict[str, Any]:
        entry = self._resolve(model_ref)
        gov = self._governance(entry)
        if gov.get("status") != GOV_PENDING:
            raise GovernanceError("Model must be in 'pending_approval' to approve")
        gov.update(
            {
                "status": GOV_APPROVED,
                "reviewer": reviewer,
                "reviewed_at": dt.datetime.now(dt.UTC).isoformat(),
                "review_notes": notes,
            }
        )
        self._set_governance(entry, gov)
        self._session.commit()
        logger.info("Model %s approved by %s", entry.version, reviewer)
        return self.get_status(model_ref)

    def reject(
        self, model_ref: str | int, reviewer: str, notes: str | None = None
    ) -> dict[str, Any]:
        entry = self._resolve(model_ref)
        gov = self._governance(entry)
        if gov.get("status") not in (GOV_PENDING, GOV_TRAINED):
            raise GovernanceError("Only pending/trained models can be rejected")
        gov.update(
            {
                "status": GOV_REJECTED,
                "production_ready": False,
                "reviewer": reviewer,
                "reviewed_at": dt.datetime.now(dt.UTC).isoformat(),
                "review_notes": notes,
            }
        )
        self._set_governance(entry, gov)
        self._session.commit()
        logger.info("Model %s rejected by %s", entry.version, reviewer)
        return self.get_status(model_ref)

    def mark_production_ready(self, model_ref: str | int, reviewer: str) -> dict[str, Any]:
        entry = self._resolve(model_ref)
        gov = self._governance(entry)
        if gov.get("status") != GOV_APPROVED:
            raise GovernanceError("Model must be 'approved' before production-ready marking")
        gov.update(
            {
                "status": GOV_PRODUCTION,
                "production_ready": True,
                "promoted_by": reviewer,
                "promoted_at": dt.datetime.now(dt.UTC).isoformat(),
            }
        )
        self._set_governance(entry, gov)
        self._session.commit()
        logger.info("Model %s marked production-ready by %s", entry.version, reviewer)
        return self.get_status(model_ref)

    # ----------------------------------------------------------------- queries
    def get_status(self, model_ref: str | int) -> dict[str, Any]:
        entry = self._resolve(model_ref)
        gov = self._governance(entry)
        return {
            "id": entry.id,
            "name": entry.name,
            "version": entry.version,
            "gate_status": (entry.params or {}).get("status", "unknown"),
            "governance_status": gov.get("status", GOV_TRAINED),
            "production_ready": bool(gov.get("production_ready", False)),
            "reviewer": gov.get("reviewer"),
            "reviewed_at": gov.get("reviewed_at"),
            "governance": gov,
        }

    def list_by_state(self, state: str | None = None) -> list[dict[str, Any]]:
        if state is not None and state not in _VALID_STATES:
            raise GovernanceError(f"Unknown governance state: {state}")
        entries = self._session.query(ModelRegistry).order_by(ModelRegistry.created_at.desc()).all()
        out = []
        for entry in entries:
            status = self.get_status(entry.id)
            if state is None or status["governance_status"] == state:
                out.append(status)
        return out
