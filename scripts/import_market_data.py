#!/usr/bin/env python3
"""Idempotent, bounded-batch import of listed NEPSE equity and index histories."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.session import SessionLocal  # noqa: E402
from app.services.nepal_market_data import NepalMarketDataImporter  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    batch_size = 5
    max_batches = int(os.environ.get("MARKET_DATA_IMPORT_MAX_BATCHES", "0"))
    start_offset = int(os.environ.get("MARKET_DATA_IMPORT_START_OFFSET", "0"))
    start_date = os.environ.get("MARKET_DATA_IMPORT_START_DATE") or None
    end_date = os.environ.get("MARKET_DATA_IMPORT_END_DATE") or None
    if max_batches < 0:
        print("MARKET_DATA_IMPORT_FAILED max_batches cannot be negative", flush=True)
        return 2
    if start_offset < 0 or start_offset % batch_size != 0:
        print(
            "MARKET_DATA_IMPORT_FAILED start_offset must be a non-negative multiple of 5",
            flush=True,
        )
        return 2

    importer = NepalMarketDataImporter(timeout_seconds=25.0)
    session = SessionLocal()
    total = {
        "catalogue_symbols": 0,
        "batches_attempted": 0,
        "symbols_succeeded": 0,
        "rows_fetched": 0,
        "rows_inserted": 0,
        "rows_updated": 0,
        "rows_rejected": 0,
        "download_errors": 0,
    }
    all_errors: list[dict[str, str]] = []

    try:
        catalogue = importer.list_tradable_equities()
        total["catalogue_symbols"] = len(catalogue)
        if start_offset > len(catalogue):
            raise ValueError(
                f"start_offset {start_offset} exceeds catalogue size {len(catalogue)}"
            )
        remaining_batches = (
            len(range(start_offset, len(catalogue), batch_size))
        )
        expected_batches = (
            remaining_batches
            if not max_batches
            else min(max_batches, remaining_batches)
        )
        print(
            "MARKET_DATA_IMPORT_START "
            + json.dumps({
                "catalogue_symbols": len(catalogue),
                "batch_size": batch_size,
                "start_offset": start_offset,
                "max_batches": max_batches or "all",
                "expected_batches_this_run": expected_batches,
                "start_date": start_date,
                "end_date": end_date,
            }),
            flush=True,
        )

        offsets = range(start_offset, len(catalogue), batch_size)
        for batch_number, offset in enumerate(
            offsets, start=(start_offset // batch_size) + 1
        ):
            if max_batches and batch_number > max_batches:
                break

            result = importer.sync_equities(
                offset=offset,
                batch_size=batch_size,
                start_date=start_date,
                end_date=end_date,
                dry_run=False,
            )
            total["batches_attempted"] += 1
            for key in (
                "symbols_succeeded",
                "rows_fetched",
                "rows_inserted",
                "rows_updated",
                "rows_rejected",
            ):
                total[key] += int(result.get(key, 0) or 0)
            errors = result.get("errors", [])
            total["download_errors"] += len(errors)
            all_errors.extend(
                {"symbol": str(e.get("symbol", "")), "error": str(e.get("error", ""))[:160]}
                for e in errors[:10]
                if isinstance(e, dict)
            )

            print(
                "MARKET_DATA_IMPORT_BATCH "
                + json.dumps({
                    "batch": batch_number,
                    "offset": offset,
                    "symbols_succeeded": result.get("symbols_succeeded", 0),
                    "symbols_requested": result.get("symbols_requested", 0),
                    "rows_fetched": result.get("rows_fetched", 0),
                    "rows_inserted": result.get("rows_inserted", 0),
                    "rows_updated": result.get("rows_updated", 0),
                    "rows_rejected": result.get("rows_rejected", 0),
                    "errors": errors[:5],
                    "next_offset": result.get("next_offset"),
                }),
                flush=True,
            )

        index = importer.sync_nepse_index(
            start_date=start_date,
            end_date=end_date,
            dry_run=False,
        )
        print(
            "MARKET_DATA_IMPORT_INDEX "
            + json.dumps({
                "records_fetched": index.get("records_fetched", 0),
                "valid_sessions": index.get("valid_sessions", 0),
                "inserted": index.get("inserted", 0),
                "updated": index.get("updated", 0),
                "rejected": index.get("rejected", 0),
                "duplicate_dates": index.get("duplicate_dates_in_source", 0),
            }),
            flush=True,
        )
        print(
            "MARKET_DATA_IMPORT_SUMMARY "
            + json.dumps({
                **total,
                "download_errors_sample": all_errors[:20],
                "nepse_index_valid_sessions": index.get("valid_sessions", 0),
                "start_offset": start_offset,
                "catalogue_complete": (
                    total["batches_attempted"] == expected_batches
                    and start_offset + total["batches_attempted"] * batch_size
                    >= len(catalogue)
                ),
                "source": "nepal-market-data",
                "corporate_action_adjusted": False,
            }),
            flush=True,
        )
        return 0
    except Exception as exc:
        print(
            "MARKET_DATA_IMPORT_FAILED "
            + json.dumps({"error_type": type(exc).__name__, "message": str(exc)[:200]}),
            flush=True,
        )
        return 1
    finally:
        importer.close()
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
