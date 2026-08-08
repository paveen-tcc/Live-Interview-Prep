"""Configurable privacy-retention cleanup for private-alpha data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import DeliveryCoachingRecord, InterviewSession, UsageEvent
from .privacy import delete_interview_data


@dataclass(frozen=True)
class RetentionResult:
    deleted_sessions: int
    deleted_delivery_records: int
    deleted_usage_events: int


async def run_retention(
    database: AsyncSession,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> RetentionResult:
    """Apply configured terminal retention policies in one transaction."""

    current = now or datetime.now(UTC)
    transcript_cutoff = current - timedelta(days=settings.transcript_retention_days)
    draft_cutoff = current - timedelta(days=settings.draft_retention_days)
    delivery_cutoff = current - timedelta(days=settings.delivery_metrics_retention_days)
    usage_cutoff = current - timedelta(days=settings.usage_event_retention_days)

    expired_sessions = list(
        await database.scalars(
            select(InterviewSession).where(
                or_(
                    InterviewSession.ended_at < transcript_cutoff,
                    (
                        InterviewSession.ended_at.is_(None)
                        & (InterviewSession.created_at < draft_cutoff)
                    ),
                )
            )
        )
    )
    for interview in expired_sessions:
        await delete_interview_data(database, interview)

    expired_delivery = list(
        await database.scalars(
            select(DeliveryCoachingRecord)
            .join(
                InterviewSession,
                InterviewSession.id == DeliveryCoachingRecord.session_id,
            )
            .where(
                InterviewSession.ended_at.is_not(None),
                InterviewSession.ended_at < delivery_cutoff,
                DeliveryCoachingRecord.deleted_at.is_(None),
            )
        )
    )
    for record in expired_delivery:
        record.consented = False
        record.disabled_at = current
        record.deleted_at = current
        record.speech_observations = []
        record.metrics = []
        record.baseline = None
        record.observations = []
        record.suggestions = []

    deletion = await database.execute(
        delete(UsageEvent).where(UsageEvent.created_at < usage_cutoff)
    )
    await database.commit()
    return RetentionResult(
        deleted_sessions=len(expired_sessions),
        deleted_delivery_records=len(expired_delivery),
        deleted_usage_events=max(0, int(deletion.rowcount or 0)),
    )
