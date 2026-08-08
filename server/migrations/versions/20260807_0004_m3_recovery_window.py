"""Add a server-owned Realtime recovery-window timestamp.

Revision ID: 20260807_0004
Revises: 20260807_0003
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0004"
down_revision: str | None = "20260807_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("interview_sessions") as batch:
        batch.add_column(sa.Column("recovery_started_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    with op.batch_alter_table("interview_sessions") as batch:
        batch.drop_column("recovery_started_at")
