#!/usr/bin/env python3
"""Run a bounded, read-only validation of the public NEPSE history dataset."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# The API image copies the application package into /app/backend.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.nepal_market_data import NepalMarketDataImporter  # noqa: E402


def public_report(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the log output limited to safe ingestion metadata."""
    keys = (
        "source",
        "catalogue_tradable_equities",
        "batch_offset",
        "batch_size",
        "symbols_requested",
        "symbols_succeeded",
        "symbols",
        "rows_fetched",
        "rows_rejected",
        "duplicate_dates_in_source",
        "errors",
        "dry_run",
        "next_offset",
        "series",
        "role",
        "records_fetched",
        "valid_sessions",
        "rejected",
    )
    return {key: result[key] for key in keys if key in result}


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    importer = NepalMarketDataImporter(timeout_seconds=25.0)
    try:
        equities = importer.sync_equities(offset=0, batch_size=5, dry_run=True)
        index = importer.sync_nepse_index(dry_run=True)
    except Exception as exc:
        # Avoid logging URLs, credentials, or any database contents.
        print(
            "MARKET_DATA_DRY_RUN_FAILED "
            + json.dumps({"error_type": type(exc).__name__, "message": str(exc)[:240]}),
            flush=True,
        )
        return 1
    finally:
        importer.close()

    report = {
        "equities": public_report(equities),
        "index": public_report(index),
        "writes_performed": False,
        "purpose": "bounded validation only; no database rows inserted or updated",
    }
    print("MARKET_DATA_DRY_RUN " + json.dumps(report, default=str), flush=True)

    if not equities.get("symbols_succeeded") or not index.get("valid_sessions"):
        print("MARKET_DATA_DRY_RUN_WARNING source data returned no usable rows", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
