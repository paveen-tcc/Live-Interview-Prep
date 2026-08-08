"""Practice-session persistence endpoints."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, principal_from_request
from ..config import Settings
from ..database import get_database_session
from ..models import InterviewSession, UsageEvent, User
from ..operations_schemas import DeleteSessionRequest
from ..schemas import (
    CreateInterviewRequest,
    InterviewListResponse,
    InterviewResponse,
)
from ..services.privacy import delete_interview_data, privacy_hash

router = APIRouter(prefix="/api/interviews", tags=["interviews"])


@router.get("", response_model=InterviewListResponse)
async def list_interviews(
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> InterviewListResponse:
    result = await database.scalars(
        select(InterviewSession)
        .where(InterviewSession.user_id == user.id)
        .order_by(InterviewSession.created_at.desc())
    )
    return InterviewListResponse(items=list(result))


@router.post("", response_model=InterviewResponse, status_code=201)
async def create_interview(
    payload: CreateInterviewRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> InterviewSession:
    settings: Settings = request.app.state.settings
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    created_today = int(
        await database.scalar(
            select(func.count())
            .select_from(InterviewSession)
            .where(
                InterviewSession.user_id == user.id,
                InterviewSession.created_at >= day_start,
            )
        )
        or 0
    )
    if created_today >= settings.daily_interview_quota:
        next_day = day_start + timedelta(days=1)
        retry_after = max(1, round((next_day - now).total_seconds()))
        raise HTTPException(
            status_code=429,
            detail=(
                "Your private-alpha daily practice-session quota is used. "
                "Try again after the UTC quota window resets."
            ),
            headers={"Retry-After": str(retry_after)},
        )
    interview = InterviewSession(user_id=user.id, title=payload.title, created_at=now)
    database.add(interview)
    await database.flush()
    database.add(
        UsageEvent(
            user_id=user.id,
            session_id=interview.id,
            kind="session_created",
            quantity=1,
            estimated_cost_microusd=0,
            created_at=now,
        )
    )
    await database.commit()
    await database.refresh(interview)
    return interview


@router.delete("/{interview_id}", status_code=204)
async def delete_interview(
    interview_id: str,
    payload: DeleteSessionRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> Response:
    interview = await database.scalar(
        select(InterviewSession).where(
            InterviewSession.id == interview_id,
            InterviewSession.user_id == user.id,
        )
    )
    if interview is None:
        # DELETE is deliberately idempotent and does not reveal another user's IDs.
        return Response(status_code=204)
    principal = principal_from_request(request, request.app.state.settings)
    await delete_interview_data(
        database,
        interview,
        principal_hash=privacy_hash("principal", principal.subject),
    )
    await database.commit()
    return Response(status_code=204)


@router.get("/{interview_id}", response_model=InterviewResponse)
async def get_interview(
    interview_id: str,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> InterviewSession:
    interview = await database.scalar(
        select(InterviewSession).where(
            InterviewSession.id == interview_id,
            InterviewSession.user_id == user.id,
        )
    )
    if interview is None:
        raise HTTPException(status_code=404, detail="Practice session was not found.")
    return interview
