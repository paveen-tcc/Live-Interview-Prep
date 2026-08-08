"""Track transcript provenance and finalization state.

Revision ID: 20260808_0008
Revises: 20260807_0007
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260808_0008"
down_revision: str | None = "20260807_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("interview_turns") as batch:
        batch.add_column(
            sa.Column(
                "transcription_source",
                sa.String(length=24),
                nullable=False,
                server_default="legacy",
            )
        )
        batch.add_column(sa.Column("transcription_model", sa.String(length=160)))
        batch.add_column(
            sa.Column("transcription_finalized_at", sa.DateTime(timezone=True))
        )


def downgrade() -> None:
    with op.batch_alter_table("interview_turns") as batch:
        batch.drop_column("transcription_finalized_at")
        batch.drop_column("transcription_model")
        batch.drop_column("transcription_source")
