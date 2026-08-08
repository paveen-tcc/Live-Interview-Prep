"""Add idempotent evidence-backed evaluations.

Revision ID: 20260807_0005
Revises: 20260807_0004
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0005"
down_revision: str | None = "20260807_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("interview_sessions") as batch:
        batch.add_column(sa.Column("setup_snapshot", sa.JSON()))
        batch.add_column(sa.Column("setup_fingerprint", sa.String(length=64)))

    op.create_table(
        "evaluations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("evaluator_version", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=120), nullable=False),
        sa.Column("model_deployment", sa.String(length=160), nullable=False),
        sa.Column("setup_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("transcript_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("transcript_turn_count", sa.Integer(), nullable=False),
        sa.Column(
            "transcript_finalized_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("scorecard_snapshot", sa.JSON(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("run_token", sa.String(length=36)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("competency_results", sa.JSON(), nullable=False),
        sa.Column("overall_result", sa.JSON()),
        sa.Column("strengths", sa.JSON(), nullable=False),
        sa.Column("gaps", sa.JSON(), nullable=False),
        sa.Column("practice_exercises", sa.JSON(), nullable=False),
        sa.Column("uncertainty", sa.JSON(), nullable=False),
        sa.Column("failure_code", sa.String(length=80)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["interview_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )
    op.create_index("ix_evaluations_session_id", "evaluations", ["session_id"])
    op.create_index("ix_evaluations_status", "evaluations", ["status"])


def downgrade() -> None:
    op.drop_index("ix_evaluations_status", table_name="evaluations")
    op.drop_index("ix_evaluations_session_id", table_name="evaluations")
    op.drop_table("evaluations")
    with op.batch_alter_table("interview_sessions") as batch:
        batch.drop_column("setup_fingerprint")
        batch.drop_column("setup_snapshot")
