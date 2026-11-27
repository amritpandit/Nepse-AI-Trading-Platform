"""Create durable audit history for scheduled automation runs.

Revision ID: 0018_automation_run_history
Revises: 0017_signal_ledger_unique
Create Date: 2026-11-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_automation_run_history"
down_revision = "0017_signal_ledger_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automation_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("run_key", sa.String(length=120), nullable=True),
        sa.Column("batch_offset", sa.Integer(), nullable=True),
        sa.Column("records_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("error_category", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_automation_runs_job_started",
        "automation_runs",
        ["job_type", "started_at"],
    )
    op.create_index("ix_automation_runs_run_key", "automation_runs", ["run_key"])


def downgrade() -> None:
    op.drop_index("ix_automation_runs_run_key", table_name="automation_runs")
    op.drop_index("ix_automation_runs_job_started", table_name="automation_runs")
    op.drop_table("automation_runs")
