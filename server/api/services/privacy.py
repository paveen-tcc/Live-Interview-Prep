"""Idempotent, ownership-scoped deletion of private interview data."""

from __future__ import annotations

import hashlib

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    CandidateProfile,
    DeletionReceipt,
    DeliveryCoachingRecord,
    Evaluation,
    InterviewSession,
    InterviewTurn,
    JobTarget,
    Scorecard,
    Upload,
    UsageEvent,
    User,
)


def privacy_hash(namespace: str, value: str) -> str:
    """Return a stable non-reversible identifier for deletion receipts."""

    return hashlib.sha256(f"{namespace}:{value}".encode()).hexdigest()


async def delete_interview_data(
    database: AsyncSession,
    interview: InterviewSession,
    *,
    principal_hash: str | None = None,
) -> None:
    """Delete one session and setup records not shared with another session."""

    interview_id = interview.id
    profile_id = interview.profile_id
    scorecard_id = interview.scorecard_id

    await database.execute(
        update(UsageEvent)
        .where(UsageEvent.session_id == interview_id)
        .values(session_id=None)
    )
    await database.execute(
        delete(DeliveryCoachingRecord).where(
            DeliveryCoachingRecord.session_id == interview_id
        )
    )
    await database.execute(
        delete(Evaluation).where(Evaluation.session_id == interview_id)
    )
    await database.execute(
        delete(InterviewTurn).where(InterviewTurn.session_id == interview_id)
    )
    await database.execute(
        delete(InterviewSession).where(InterviewSession.id == interview_id)
    )
    await database.flush()

    if profile_id is not None:
        profile_references = await database.scalar(
            select(func.count())
            .select_from(InterviewSession)
            .where(InterviewSession.profile_id == profile_id)
        )
        if not profile_references:
            profile = await database.get(CandidateProfile, profile_id)
            if profile is not None:
                upload_id = profile.source_resume_id
                await database.execute(
                    delete(CandidateProfile).where(CandidateProfile.id == profile_id)
                )
                await database.flush()
                upload_references = await database.scalar(
                    select(func.count())
                    .select_from(CandidateProfile)
                    .where(CandidateProfile.source_resume_id == upload_id)
                )
                if not upload_references:
                    await database.execute(delete(Upload).where(Upload.id == upload_id))

    if scorecard_id is not None:
        scorecard_references = await database.scalar(
            select(func.count())
            .select_from(InterviewSession)
            .where(InterviewSession.scorecard_id == scorecard_id)
        )
        if not scorecard_references:
            scorecard = await database.get(Scorecard, scorecard_id)
            if scorecard is not None:
                target_id = scorecard.job_target_id
                await database.execute(
                    delete(Scorecard).where(Scorecard.id == scorecard_id)
                )
                await database.flush()
                target_references = await database.scalar(
                    select(func.count())
                    .select_from(Scorecard)
                    .where(Scorecard.job_target_id == target_id)
                )
                if not target_references:
                    await database.execute(
                        delete(JobTarget).where(JobTarget.id == target_id)
                    )

    if principal_hash is not None:
        target_hash = privacy_hash("session", interview_id)
        existing = await database.scalar(
            select(DeletionReceipt).where(
                DeletionReceipt.kind == "session",
                DeletionReceipt.target_hash == target_hash,
            )
        )
        if existing is None:
            database.add(
                DeletionReceipt(
                    principal_hash=principal_hash,
                    target_hash=target_hash,
                    kind="session",
                    status="completed",
                )
            )


async def delete_account_data(
    database: AsyncSession, user: User, *, principal_hash: str
) -> None:
    """Delete all user content and leave only a PII-free terminal receipt."""

    interviews = list(
        await database.scalars(
            select(InterviewSession).where(InterviewSession.user_id == user.id)
        )
    )
    for interview in interviews:
        await delete_interview_data(database, interview, principal_hash=principal_hash)

    # Remove any unattached intake data created before a session was completed.
    profiles = list(
        await database.scalars(
            select(CandidateProfile).where(CandidateProfile.user_id == user.id)
        )
    )
    upload_ids = [profile.source_resume_id for profile in profiles]
    await database.execute(
        delete(CandidateProfile).where(CandidateProfile.user_id == user.id)
    )
    if upload_ids:
        await database.execute(delete(Upload).where(Upload.id.in_(upload_ids)))
    await database.execute(delete(Upload).where(Upload.user_id == user.id))

    target_ids = list(
        await database.scalars(select(JobTarget.id).where(JobTarget.user_id == user.id))
    )
    if target_ids:
        await database.execute(
            delete(Scorecard).where(Scorecard.job_target_id.in_(target_ids))
        )
    await database.execute(delete(JobTarget).where(JobTarget.user_id == user.id))
    await database.execute(delete(UsageEvent).where(UsageEvent.user_id == user.id))
    await database.execute(delete(User).where(User.id == user.id))

    existing = await database.scalar(
        select(DeletionReceipt).where(
            DeletionReceipt.kind == "account",
            DeletionReceipt.target_hash == principal_hash,
        )
    )
    if existing is None:
        database.add(
            DeletionReceipt(
                principal_hash=principal_hash,
                target_hash=principal_hash,
                kind="account",
                status="completed",
            )
        )
