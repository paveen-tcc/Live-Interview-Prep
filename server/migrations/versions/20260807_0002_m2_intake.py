"""Create M2 resume, profile, job target, and scorecard data.

Revision ID: 20260807_0002
Revises: 20260806_0001
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0002"
down_revision: str | None = "20260806_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "uploads",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("generated_storage_key", sa.String(length=80), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=16), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("source_segments", sa.JSON(), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("generated_storage_key"),
    )
    op.create_index("ix_uploads_user_id", "uploads", ["user_id"], unique=False)
    op.create_index("ix_uploads_sha256", "uploads", ["sha256"], unique=False)
    op.create_index("ix_uploads_created_at", "uploads", ["created_at"], unique=False)

    op.create_table(
        "candidate_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("source_resume_id", sa.String(length=36), nullable=False),
        sa.Column("structured_claims", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_resume_id"], ["uploads.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_candidate_profiles_user_id",
        "candidate_profiles",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_candidate_profiles_source_resume_id",
        "candidate_profiles",
        ["source_resume_id"],
        unique=True,
    )

    op.create_table(
        "job_targets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("seniority", sa.String(length=20), nullable=False),
        sa.Column("raw_description", sa.Text(), nullable=False),
        sa.Column("structured_requirements", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_targets_user_id", "job_targets", ["user_id"], unique=False)

    op.create_table(
        "scorecards",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_target_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("competencies", sa.JSON(), nullable=False),
        sa.Column("total_weight", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_target_id"], ["job_targets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_scorecards_job_target_id",
        "scorecards",
        ["job_target_id"],
        unique=False,
    )

    with op.batch_alter_table("interview_sessions") as batch:
        batch.add_column(sa.Column("profile_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("scorecard_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_interview_sessions_profile_id",
            "candidate_profiles",
            ["profile_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_interview_sessions_scorecard_id",
            "scorecards",
            ["scorecard_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("interview_sessions") as batch:
        batch.drop_constraint("fk_interview_sessions_scorecard_id", type_="foreignkey")
        batch.drop_constraint("fk_interview_sessions_profile_id", type_="foreignkey")
        batch.drop_column("scorecard_id")
        batch.drop_column("profile_id")

    op.drop_index("ix_scorecards_job_target_id", table_name="scorecards")
    op.drop_table("scorecards")
    op.drop_index("ix_job_targets_user_id", table_name="job_targets")
    op.drop_table("job_targets")
    op.drop_index(
        "ix_candidate_profiles_source_resume_id", table_name="candidate_profiles"
    )
    op.drop_index("ix_candidate_profiles_user_id", table_name="candidate_profiles")
    op.drop_table("candidate_profiles")
    op.drop_index("ix_uploads_created_at", table_name="uploads")
    op.drop_index("ix_uploads_sha256", table_name="uploads")
    op.drop_index("ix_uploads_user_id", table_name="uploads")
    op.drop_table("uploads")
