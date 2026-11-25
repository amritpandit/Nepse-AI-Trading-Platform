#!/usr/bin/env python3
"""Read-only, row-level audit of source OHLCV validation rejects.

This utility fetches public CSVs only. It never creates a database session,
reads a database URL, or inserts/updates application data.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import httpx

# Match the repository's existing script entry points when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.nepal_market_data import (  # noqa: E402
    NepalMarketDataImporter,
    _decimal,
    _int_value,
    _parse_date,
)


def audit_symbol(
    importer: NepalMarketDataImporter,
    symbol: str,
    max_samples: int,
) -> dict[str, Any]:
    """Apply the importer's row validation and explain rejected source rows."""
    file_symbol = symbol.replace("/", "-")
    rows = importer._get_csv(f"data/nepse/{file_symbol}.csv")
    reasons: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    valid_rows = 0
    rejected_rows = 0
    duplicate_dates = 0
    valid_dates: set[date] = set()

    for line_number, row in enumerate(rows, start=2):
        row_date = _parse_date(row.get("published_date"))
        row_reasons: list[str] = []
        if row_date is None:
            row_reasons.append("invalid_date")
        else:
            open_price = _decimal(row.get("open"))
            high_price = _decimal(row.get("high"))
            low_price = _decimal(row.get("low"))
            close_price = _decimal(row.get("close"))
            volume = _int_value(row.get("traded_quantity")) or 0
            ohlc = [
                value
                for value in (open_price, high_price, low_price, close_price)
                if value is not None
            ]

            if len(ohlc) != 4:
                row_reasons.append("missing_or_unparseable_ohlc")
            elif any(value <= 0 for value in ohlc):
                row_reasons.append("nonpositive_ohlc")
            else:
                # Mirror the importer's full OHLC envelope checks while keeping
                # the rejection explanations specific and non-overlapping where possible.
                if high_price is not None and high_price < max(open_price, close_price):
                    row_reasons.append("high_below_open_or_close")
                if low_price is not None and low_price > min(open_price, close_price):
                    row_reasons.append("low_above_open_or_close")
                if high_price is not None and low_price is not None and high_price < low_price:
                    row_reasons.append("high_below_low")

            if volume < 0:
                row_reasons.append("negative_volume")

            if not row_reasons:
                valid_rows += 1
                if row_date in valid_dates:
                    duplicate_dates += 1
                valid_dates.add(row_date)

        if row_reasons:
            rejected_rows += 1
            reasons.update(row_reasons)
            if len(samples) < max_samples:
                samples.append(
                    {
                        "csv_line": line_number,
                        "date": row.get("published_date"),
                        "reasons": row_reasons,
                        "open": row.get("open"),
                        "high": row.get("high"),
                        "low": row.get("low"),
                        "close": row.get("close"),
                        "volume": row.get("traded_quantity"),
                    }
                )

    return {
        "symbol": symbol,
        "source_rows": len(rows),
        "valid_rows_including_valid_duplicates": valid_rows,
        "valid_unique_sessions": len(valid_dates),
        "duplicate_valid_dates": duplicate_dates,
        "rejected_rows": rejected_rows,
        "rejection_reason_counts": dict(sorted(reasons.items())),
        "rejected_row_samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit row-level OHLCV rejects without connecting to PostgreSQL."
    )
    parser.add_argument(
        "--symbols",
        default="ADBL,AHPC",
        help="Comma-separated listed ordinary-share symbols (default: ADBL,AHPC).",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=20,
        help="Maximum rejected rows to show per symbol (default: 20).",
    )
    args = parser.parse_args()
    if args.max_samples < 0:
        parser.error("--max-samples must be non-negative")

    symbols = sorted(
        {
            item.strip().upper()
            for item in args.symbols.split(",")
            if item.strip()
        }
    )
    if not symbols:
        parser.error("At least one symbol is required.")

    importer = NepalMarketDataImporter(timeout_seconds=25.0)
    try:
        listed = {
            row["symbol"].strip().upper()
            for row in importer.list_tradable_equities()
            if row.get("symbol", "").strip()
        }
        unavailable = [symbol for symbol in symbols if symbol not in listed]
        if unavailable:
            print(
                json.dumps(
                    {
                        "error": "Symbols are not listed ordinary shares in the source catalogue.",
                        "symbols": unavailable,
                    }
                ),
                file=sys.stderr,
            )
            return 2

        results = [
            audit_symbol(importer, symbol, args.max_samples)
            for symbol in symbols
        ]
        print(
            json.dumps(
                {
                    "source": "nepal-market-data",
                    "database_connection_attempted": False,
                    "writes_performed": False,
                    "validation": (
                        "Matches NepalMarketDataImporter.sync_equities OHLCV rules; "
                        "rejected values are reported, never corrected."
                    ),
                    "symbols": results,
                },
                indent=2,
                default=str,
            )
        )
        return 0
    except httpx.HTTPError as exc:
        print(
            json.dumps(
                {
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:240],
                    "writes_performed": False,
                }
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        importer.close()


if __name__ == "__main__":
    raise SystemExit(main())
