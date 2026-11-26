"""Shared authentication for private machine-to-machine automation endpoints."""
from __future__ import annotations

import secrets

from app.core.config import settings
from fastapi import HTTPException, status


def require_automation_token(candidate: str | None) -> None:
    """Reject requests unless the private automation secret is configured and matches."""
    expected = settings.signal_automation_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduled automation is not configured.",
        )
    if not candidate or not secrets.compare_digest(candidate, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid automation credential.",
        )
