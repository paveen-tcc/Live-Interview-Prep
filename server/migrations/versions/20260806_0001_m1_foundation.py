"""Create M1 users and interview sessions.

Revision ID: 20260806_0001
Revises:
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("auth_subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_auth_subject", "users", ["auth_subject"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=False)
    op.create_table(
        "interview_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_interview_sessions_created_at",
        "interview_sessions",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_interview_sessions_status",
        "interview_sessions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_interview_sessions_user_id",
        "interview_sessions",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_interview_sessions_user_id", table_name="interview_sessions")
    op.drop_index("ix_interview_sessions_status", table_name="interview_sessions")
    op.drop_index("ix_interview_sessions_created_at", table_name="interview_sessions")
    op.drop_table("interview_sessions")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_auth_subject", table_name="users")
    op.drop_table("users")
