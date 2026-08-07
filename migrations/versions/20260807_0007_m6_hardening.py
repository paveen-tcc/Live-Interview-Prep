"""Add private-alpha usage telemetry and deletion receipts.

Revision ID: 20260807_0007
Revises: 20260807_0006
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0007"
down_revision: str | None = "20260807_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36)),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_microusd", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["interview_sessions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"])
    op.create_index("ix_usage_events_session_id", "usage_events", ["session_id"])
    op.create_index("ix_usage_events_kind", "usage_events", ["kind"])
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])

    op.create_table(
        "deletion_receipts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("principal_hash", sa.String(length=64), nullable=False),
        sa.Column("target_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "target_hash"),
    )
    op.create_index(
        "ix_deletion_receipts_principal_hash",
        "deletion_receipts",
        ["principal_hash"],
    )
    op.create_index(
        "ix_deletion_receipts_target_hash", "deletion_receipts", ["target_hash"]
    )
    op.create_index("ix_deletion_receipts_kind", "deletion_receipts", ["kind"])
    op.create_index(
        "ix_deletion_receipts_completed_at",
        "deletion_receipts",
        ["completed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_deletion_receipts_completed_at", table_name="deletion_receipts")
    op.drop_index("ix_deletion_receipts_kind", table_name="deletion_receipts")
    op.drop_index("ix_deletion_receipts_target_hash", table_name="deletion_receipts")
    op.drop_index("ix_deletion_receipts_principal_hash", table_name="deletion_receipts")
    op.drop_table("deletion_receipts")
    op.drop_index("ix_usage_events_created_at", table_name="usage_events")
    op.drop_index("ix_usage_events_kind", table_name="usage_events")
    op.drop_index("ix_usage_events_session_id", table_name="usage_events")
    op.drop_index("ix_usage_events_user_id", table_name="usage_events")
    op.drop_table("usage_events")
