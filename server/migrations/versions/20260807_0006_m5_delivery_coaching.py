"""Add consent-gated speaking delivery coaching.

Revision ID: 20260807_0006
Revises: 20260807_0005
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0006"
down_revision: str | None = "20260807_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delivery_coaching",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("consented", sa.Boolean(), nullable=False),
        sa.Column("consent_version", sa.String(length=80)),
        sa.Column("consented_at", sa.DateTime(timezone=True)),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("speech_observations", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("baseline", sa.JSON()),
        sa.Column("observations", sa.JSON(), nullable=False),
        sa.Column("suggestions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["interview_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )
    op.create_index(
        "ix_delivery_coaching_session_id", "delivery_coaching", ["session_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_delivery_coaching_session_id", table_name="delivery_coaching")
    op.drop_table("delivery_coaching")
