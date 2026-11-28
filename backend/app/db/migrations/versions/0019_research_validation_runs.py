"""Persist reproducible universe-level score validation runs.

Revision ID: 0019_research_validation_runs
Revises: 0018_automation_run_history
Create Date: 2026-11-28
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019_research_validation_runs"
down_revision = "0018_automation_run_history"
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)


def upgrade() -> None:
    op.create_table(
        "research_validation_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("sector_filter", sa.String(length=120), nullable=True),
        sa.Column("requested_limit", sa.Integer(), nullable=False),
        sa.Column("requested_offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("min_samples", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("symbols_analyzed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rank_eligible_symbols", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latest_price_date", sa.Date(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parameters", json_type, nullable=False),
        sa.Column("result", json_type, nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_research_validation_runs_user_started",
        "research_validation_runs",
        ["user_id", "started_at"],
    )
    op.create_index(
        "ix_research_validation_runs_model_started",
        "research_validation_runs",
        ["model_version", "started_at"],
    )
    op.create_index(
        "ix_research_validation_runs_status",
        "research_validation_runs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_validation_runs_status", table_name="research_validation_runs")
    op.drop_index(
        "ix_research_validation_runs_model_started",
        table_name="research_validation_runs",
    )
    op.drop_index(
        "ix_research_validation_runs_user_started",
        table_name="research_validation_runs",
    )
    op.drop_table("research_validation_runs")
