"""Persistence models for the web application."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    auth_subject: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    interviews: Mapped[list[InterviewSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    uploads: Mapped[list[Upload]] = relationship(cascade="all, delete-orphan")
    candidate_profiles: Mapped[list[CandidateProfile]] = relationship(
        cascade="all, delete-orphan"
    )
    job_targets: Mapped[list[JobTarget]] = relationship(cascade="all, delete-orphan")
    usage_events: Mapped[list[UsageEvent]] = relationship(cascade="all, delete-orphan")


class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(120), default="Untitled practice session")
    status: Mapped[str] = mapped_column(String(40), default="DRAFT", index=True)
    profile_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("candidate_profiles.id", ondelete="SET NULL")
    )
    scorecard_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scorecards.id", ondelete="SET NULL")
    )
    duration_minutes: Mapped[int] = mapped_column(Integer, default=15)
    interview_type: Mapped[str] = mapped_column(
        String(40), default="technical_behavioral"
    )
    input_mode: Mapped[str] = mapped_column(String(20), default="voice")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovery_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    prompt_version: Mapped[str | None] = mapped_column(String(80))
    setup_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON)
    setup_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    user: Mapped[User] = relationship(back_populates="interviews")
    turns: Mapped[list[InterviewTurn]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="InterviewTurn.sequence",
    )
    evaluation: Mapped[Evaluation | None] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        uselist=False,
    )
    delivery_coaching: Mapped[DeliveryCoachingRecord | None] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        uselist=False,
    )


class InterviewTurn(Base):
    __tablename__ = "interview_turns"
    __table_args__ = (
        UniqueConstraint("session_id", "client_turn_id"),
        UniqueConstraint("session_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    client_turn_id: Mapped[str] = mapped_column(String(96))
    sequence: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(16))
    transcript: Mapped[str] = mapped_column(Text)
    transcription_source: Mapped[str] = mapped_column(
        String(24), default="legacy", server_default="legacy"
    )
    transcription_model: Mapped[str | None] = mapped_column(String(160))
    transcription_finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    delivery_status: Mapped[str] = mapped_column(String(20), default="acknowledged")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    session: Mapped[InterviewSession] = relationship(back_populates="turns")


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    schema_version: Mapped[str] = mapped_column(String(80))
    evaluator_version: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(120))
    model_deployment: Mapped[str] = mapped_column(String(160))
    setup_fingerprint: Mapped[str] = mapped_column(String(64))
    transcript_fingerprint: Mapped[str] = mapped_column(String(64))
    transcript_turn_count: Mapped[int] = mapped_column(Integer)
    transcript_finalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scorecard_snapshot: Mapped[dict[str, object]] = mapped_column(JSON)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    run_token: Mapped[str | None] = mapped_column(String(36))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    competency_results: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    overall_result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    strengths: Mapped[list[str]] = mapped_column(JSON)
    gaps: Mapped[list[str]] = mapped_column(JSON)
    practice_exercises: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    uncertainty: Mapped[list[str]] = mapped_column(JSON)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    session: Mapped[InterviewSession] = relationship(back_populates="evaluation")


class DeliveryCoachingRecord(Base):
    __tablename__ = "delivery_coaching"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    consented: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_version: Mapped[str | None] = mapped_column(String(80))
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    speech_observations: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    metrics: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    baseline: Mapped[dict[str, object] | None] = mapped_column(JSON)
    observations: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    suggestions: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    session: Mapped[InterviewSession] = relationship(back_populates="delivery_coaching")


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    generated_storage_key: Mapped[str] = mapped_column(
        String(80), unique=True, default=lambda: f"resume/{new_id()}"
    )
    original_filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(16))
    media_type: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    extracted_text: Mapped[str] = mapped_column(Text)
    source_segments: Mapped[list[dict[str, str]]] = mapped_column(JSON)
    retention_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_resume_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("uploads.id", ondelete="RESTRICT"),
        unique=True,
        index=True,
    )
    structured_claims: Mapped[dict[str, object]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class JobTarget(Base):
    __tablename__ = "job_targets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(160))
    seniority: Mapped[str] = mapped_column(String(20))
    raw_description: Mapped[str] = mapped_column(Text)
    structured_requirements: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Scorecard(Base):
    __tablename__ = "scorecards"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_target_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("job_targets.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    competencies: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    total_weight: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class UsageEvent(Base):
    """User-scoped, content-free alpha usage and cost telemetry."""

    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("interview_sessions.id", ondelete="SET NULL"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(48), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    estimated_cost_microusd: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )


class DeletionReceipt(Base):
    """PII-free terminal receipt for idempotent privacy deletion requests."""

    __tablename__ = "deletion_receipts"
    __table_args__ = (UniqueConstraint("kind", "target_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    principal_hash: Mapped[str] = mapped_column(String(64), index=True)
    target_hash: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    status: Mapped[str] = mapped_column(String(24), default="completed")
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
