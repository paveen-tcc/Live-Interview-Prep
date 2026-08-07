"""Add M3 Realtime interview state and idempotent transcript turns.

Revision ID: 20260807_0003
Revises: 20260807_0002
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0003"
down_revision: str | None = "20260807_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("interview_sessions") as batch:
        batch.add_column(
            sa.Column(
                "duration_minutes", sa.Integer(), nullable=False, server_default="15"
            )
        )
        batch.add_column(
            sa.Column(
                "interview_type",
                sa.String(length=40),
                nullable=False,
                server_default="technical_behavioral",
            )
        )
        batch.add_column(
            sa.Column(
                "input_mode",
                sa.String(length=20),
                nullable=False,
                server_default="voice",
            )
        )
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("ended_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_connected_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("prompt_version", sa.String(length=80)))

    op.create_table(
        "interview_turns",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("client_turn_id", sa.String(length=96), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(length=16), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column(
            "delivery_status",
            sa.String(length=20),
            nullable=False,
            server_default="acknowledged",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["interview_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "client_turn_id"),
        sa.UniqueConstraint("session_id", "sequence"),
    )
    op.create_index(
        "ix_interview_turns_session_id",
        "interview_turns",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_interview_turns_session_id", table_name="interview_turns")
    op.drop_table("interview_turns")
    with op.batch_alter_table("interview_sessions") as batch:
        batch.drop_column("prompt_version")
        batch.drop_column("last_connected_at")
        batch.drop_column("ended_at")
        batch.drop_column("started_at")
        batch.drop_column("input_mode")
        batch.drop_column("interview_type")
        batch.drop_column("duration_minutes")
