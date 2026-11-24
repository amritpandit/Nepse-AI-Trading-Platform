#!/usr/bin/env python3
"""One-time, non-destructive transfer of the user's small portfolio dataset.

The migration runs inside Render. Password hashes and portfolio records move
directly between the two PostgreSQL databases and are never printed or returned
to the assistant. The existing source database is read-only from this script.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import psycopg
from psycopg.rows import dict_row

TABLES = (
    "users",
    "portfolio_accounts",
    "portfolio_positions",
    "portfolio_transactions",
)
SEQUENCE_TABLES = (
    "portfolio_accounts",
    "portfolio_positions",
    "portfolio_transactions",
)


def normalize_url(value: str) -> str:
    """Convert SQLAlchemy's psycopg dialect URL to a libpq URL."""
    return (
        value.replace("postgresql+psycopg://", "postgresql://", 1)
        .replace("postgresql+psycopg2://", "postgresql://", 1)
    )


def get_columns(cur: Any, table: str) -> list[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    return [row["column_name"] for row in cur.fetchall()]


def digest(cur: Any, table: str) -> tuple[int, str]:
    cur.execute(
        f"""
        SELECT COUNT(*)::bigint AS row_count,
               md5(COALESCE(string_agg(row_to_json(t)::text, '|' ORDER BY id), '')) AS checksum
        FROM (SELECT * FROM public."{table}" ORDER BY id) AS t
        """
    )
    row = cur.fetchone()
    return int(row["row_count"]), str(row["checksum"])


def main() -> int:
    source_url = os.environ.get("SOURCE_DATABASE_URL", "").strip()
    target_url = os.environ.get("TARGET_DATABASE_URL", "").strip()
    if not source_url or not target_url:
        print("Migration refused: both database URLs must be configured.", file=sys.stderr)
        return 2

    source_url = normalize_url(source_url)
    target_url = normalize_url(target_url)

    try:
        with (
            psycopg.connect(source_url, row_factory=dict_row, connect_timeout=10) as source,
            psycopg.connect(target_url, row_factory=dict_row, connect_timeout=10) as target,
        ):
            with source.cursor() as source_cur, target.transaction():
                with target.cursor() as target_cur:
                    source_cur.execute("SELECT version_num FROM alembic_version")
                    source_revision = source_cur.fetchone()["version_num"]
                    target_cur.execute("SELECT version_num FROM alembic_version")
                    target_revision = target_cur.fetchone()["version_num"]
                    if source_revision != target_revision:
                        raise RuntimeError(
                            f"Schema revision mismatch: source={source_revision}, target={target_revision}"
                        )
                    if source_revision != "0016_stock_instrument_type":
                        raise RuntimeError("Unexpected schema revision; refusing data transfer.")

                    copied: dict[str, int] = {}
                    for table in TABLES:
                        source_columns = get_columns(source_cur, table)
                        target_columns = get_columns(target_cur, table)
                        if not source_columns or source_columns != target_columns:
                            raise RuntimeError(f"Column mismatch for {table}; refusing data transfer.")

                        source_cur.execute(f'SELECT * FROM public."{table}" ORDER BY id')
                        rows = source_cur.fetchall()
                        source_count, source_hash = digest(source_cur, table)
                        target_count, target_hash = digest(target_cur, table)

                        if target_count:
                            if (target_count, target_hash) != (source_count, source_hash):
                                raise RuntimeError(
                                    f"Target table {table} is not empty and does not match source; refusing overwrite."
                                )
                            copied[table] = source_count
                            continue

                        if rows:
                            quoted_columns = ", ".join(f'"{col}"' for col in source_columns)
                            placeholders = ", ".join(["%s"] * len(source_columns))
                            statement = (
                                f'INSERT INTO public."{table}" ({quoted_columns}) '
                                f"VALUES ({placeholders})"
                            )
                            values = [
                                tuple(row[column] for column in source_columns)
                                for row in rows
                            ]
                            target_cur.executemany(statement, values)

                        target_count, target_hash = digest(target_cur, table)
                        if (target_count, target_hash) != (source_count, source_hash):
                            raise RuntimeError(f"Post-copy validation failed for {table}.")
                        copied[table] = target_count

                    for table in SEQUENCE_TABLES:
                        target_cur.execute(
                            "SELECT pg_get_serial_sequence(%s, 'id') AS sequence_name",
                            (f"public.{table}",),
                        )
                        sequence_name = target_cur.fetchone()["sequence_name"]
                        if sequence_name:
                            target_cur.execute(
                                f"""
                                SELECT setval(
                                    %s::regclass,
                                    GREATEST(COALESCE((SELECT MAX(id) FROM public."{table}"), 1), 1),
                                    EXISTS(SELECT 1 FROM public."{table}")
                                )
                                """,
                                (sequence_name,),
                            )

                    # Final verification while still inside the target transaction.
                    for table in TABLES:
                        source_count, source_hash = digest(source_cur, table)
                        target_count, target_hash = digest(target_cur, table)
                        if (source_count, source_hash) != (target_count, target_hash):
                            raise RuntimeError(f"Final verification failed for {table}.")

            total = sum(copied.values())
            print(
                "MIGRATION_OK "
                + " ".join(f"{table}={copied[table]}" for table in TABLES)
                + f" total_rows={total} schema={source_revision}"
            )
            return 0
    except Exception as exc:
        # Do not print row contents, connection strings, or driver diagnostics
        # that might contain credentials.
        print(f"MIGRATION_FAILED {type(exc).__name__}: {str(exc)[:240]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
