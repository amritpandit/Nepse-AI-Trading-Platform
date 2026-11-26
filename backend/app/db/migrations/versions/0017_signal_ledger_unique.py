"""Add unique daily research signal snapshot index.

Revision ID: 0017_signal_ledger_unique
Revises: 0016_stock_instrument_type
Create Date: 2026-11-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_signal_ledger_unique"
down_revision = "0016_stock_instrument_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {index["name"] for index in inspector.get_indexes("signals")}
    if "uq_signals_sector_stock_score_session" not in existing:
        op.create_index(
            "uq_signals_sector_stock_score_session",
            "signals",
            ["stock_id", "date", "signal_type", "provider", "model_source"],
            unique=True,
            postgresql_where=sa.text(
                "provider = 'research_score' AND model_source = 'sector-stock-score-v1'"
            ),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {index["name"] for index in inspector.get_indexes("signals")}
    if "uq_signals_sector_stock_score_session" in existing:
        op.drop_index("uq_signals_sector_stock_score_session", table_name="signals")
