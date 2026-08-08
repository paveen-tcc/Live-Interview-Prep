"""Owned M3 Realtime session, recovery, and transcript endpoints."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.interview import (
    ACTIVE_INTERVIEW_STATES,
    CONNECTION_TRANSITIONS,
    SUPPORTED_DURATIONS,
)
from prompts.interview_v1 import (
    PROMPT_VERSION,
    build_interview_prompt_from_snapshot,
    build_setup_snapshot,
    setup_fingerprint,
)

from ..auth import get_current_user
from ..config import Settings
from ..database import get_database_session
from ..models import (
    CandidateProfile,
    InterviewSession,
    InterviewTurn,
    JobTarget,
    Scorecard,
    UsageEvent,
    User,
)
from ..realtime_schemas import (
    ConnectionStateRequest,
    InterviewRuntimeResponse,
    InterviewTurnBatchRequest,
    InterviewTurnResponse,
    RealtimeClientSecretRequest,
    RealtimeClientSecretResponse,
)
from ..services.evaluation_jobs import run_evaluation_job
from ..services.realtime import RealtimeServiceError, create_realtime_client_secret

router = APIRouter(prefix="/api/interviews", tags=["realtime"])


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _scheduled_end(interview: InterviewSession) -> datetime | None:
    started_at = _ensure_utc(interview.started_at)
    if started_at is None:
        return None
    return started_at + timedelta(minutes=interview.duration_minutes)


async def _finalize_if_expired(
    interview: InterviewSession, database: AsyncSession
) -> bool:
    ends_at = _scheduled_end(interview)
    if ends_at is None or interview.ended_at is not None or datetime.now(UTC) < ends_at:
        return False
    interview.ended_at = ends_at
    interview.status = "TRANSCRIPT_FINALIZING"
    interview.recovery_started_at = None
    await database.commit()
    return True


async def _owned_interview(
    database: AsyncSession,
    user: User,
    interview_id: str,
    *,
    for_update: bool = False,
) -> InterviewSession:
    statement = select(InterviewSession).where(
        InterviewSession.id == interview_id,
        InterviewSession.user_id == user.id,
    )
    if for_update:
        statement = statement.with_for_update()
    interview = await database.scalar(statement)
    if interview is None:
        raise HTTPException(status_code=404, detail="Practice session was not found.")
    return interview


async def _turns(database: AsyncSession, interview_id: str) -> list[InterviewTurn]:
    result = await database.scalars(
        select(InterviewTurn)
        .where(InterviewTurn.session_id == interview_id)
        .order_by(InterviewTurn.sequence)
    )
    return list(result)


def _runtime_response(
    interview: InterviewSession,
    turns: list[InterviewTurn],
    settings: Settings,
) -> InterviewRuntimeResponse:
    now = datetime.now(UTC)
    ends_at = _scheduled_end(interview)
    normalized_turns = [
        InterviewTurnResponse(
            id=turn.id,
            client_turn_id=turn.client_turn_id,
            sequence=turn.sequence,
            speaker=turn.speaker,
            transcript=turn.transcript,
            transcription_source=turn.transcription_source,
            transcription_model=turn.transcription_model,
            transcription_finalized_at=_ensure_utc(turn.transcription_finalized_at),
            delivery_status=turn.delivery_status,
            started_at=_ensure_utc(turn.started_at),
            ended_at=_ensure_utc(turn.ended_at),
        )
        for turn in turns
    ]
    return InterviewRuntimeResponse(
        interview_id=interview.id,
        status=interview.status,
        input_mode=interview.input_mode,
        duration_minutes=interview.duration_minutes,
        started_at=_ensure_utc(interview.started_at),
        ends_at=ends_at,
        server_now=now,
        typed_answer_max_characters=settings.typed_answer_max_characters,
        turns=normalized_turns,
    )


async def _enforce_secret_rate_limit(
    request: Request,
    database: AsyncSession,
    *,
    user_id: str,
    interview_id: str,
) -> None:
    settings: Settings = request.app.state.settings
    recent_persisted = int(
        await database.scalar(
            select(func.count())
            .select_from(UsageEvent)
            .where(
                UsageEvent.user_id == user_id,
                UsageEvent.session_id == interview_id,
                UsageEvent.kind == "realtime_secret_created",
                UsageEvent.created_at >= datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        or 0
    )
    if recent_persisted >= settings.realtime_client_secret_rate_limit:
        raise HTTPException(
            status_code=429,
            detail="Too many Realtime connection attempts. Wait a minute and retry.",
            headers={"Retry-After": "60"},
        )
    now = time.monotonic()
    attempts: list[float] = request.app.state.realtime_secret_attempts.setdefault(
        interview_id, []
    )
    attempts[:] = [attempt for attempt in attempts if now - attempt < 60]
    if len(attempts) >= settings.realtime_client_secret_rate_limit:
        raise HTTPException(
            status_code=429,
            detail="Too many Realtime connection attempts. Wait a minute and retry.",
            headers={"Retry-After": "60"},
        )
    attempts.append(now)


def _enforce_reconnect_window(interview: InterviewSession, settings: Settings) -> None:
    if interview.status not in {"RECONNECTING", "FAILED_RECOVERABLE"}:
        return
    recovery_started_at = _ensure_utc(interview.recovery_started_at)
    if recovery_started_at is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "The Realtime recovery state is incomplete. Restart from the session."
            ),
        )
    deadline = recovery_started_at + timedelta(
        seconds=settings.realtime_reconnect_window_seconds
    )
    if datetime.now(UTC) > deadline:
        raise HTTPException(
            status_code=409,
            detail=(
                "The Realtime recovery window has expired. Stop this interview "
                "to save its transcript."
            ),
        )


@router.post(
    "/{interview_id}/realtime-client-secret",
    response_model=RealtimeClientSecretResponse,
)
async def realtime_client_secret(
    interview_id: str,
    payload: RealtimeClientSecretRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> RealtimeClientSecretResponse:
    settings: Settings = request.app.state.settings
    interview = await _owned_interview(database, user, interview_id, for_update=True)
    await _finalize_if_expired(interview, database)
    if payload.duration_minutes not in SUPPORTED_DURATIONS:
        raise HTTPException(
            status_code=422,
            detail="Interview duration must be 15, 30, 45, or 60 minutes.",
        )
    if payload.input_mode == "text_dev" and not settings.enable_text_dev_mode:
        raise HTTPException(
            status_code=403,
            detail="Developer text input is disabled by the server.",
        )
    if interview.setup_snapshot is None and (
        not interview.profile_id or not interview.scorecard_id
    ):
        raise HTTPException(
            status_code=409,
            detail="Finish the candidate profile and scorecard before preflight.",
        )
    if interview.ended_at is not None:
        raise HTTPException(status_code=409, detail="This interview has already ended.")
    cache_key = (
        payload.input_mode,
        payload.duration_minutes,
        payload.interview_type,
        _ensure_utc(interview.recovery_started_at),
    )
    cached = request.app.state.realtime_secret_cache.get(interview.id)
    if (
        cached is not None
        and cached["key"] == cache_key
        and cached["response"].expires_at > int(time.time()) + 5
        and interview.status in {"CONNECTING", "RECONNECTING"}
    ):
        return cached["response"]
    allowed_secret_states = {"SCORECARD_READY", "RECONNECTING", "FAILED_RECOVERABLE"}
    if interview.status not in allowed_secret_states:
        raise HTTPException(
            status_code=409,
            detail="This interview is not ready to create a Realtime connection.",
        )
    _enforce_reconnect_window(interview, settings)
    if interview.started_at and (
        interview.input_mode != payload.input_mode
        or interview.duration_minutes != payload.duration_minutes
        or interview.interview_type != payload.interview_type
    ):
        raise HTTPException(
            status_code=409,
            detail="Input mode, duration, and interview type are frozen after start.",
        )

    prior_turns = await _turns(database, interview.id)

    interview.input_mode = payload.input_mode
    interview.duration_minutes = payload.duration_minutes
    interview.interview_type = payload.interview_type
    if interview.prompt_version is None:
        interview.prompt_version = PROMPT_VERSION
    elif interview.prompt_version != PROMPT_VERSION:
        raise HTTPException(
            status_code=409,
            detail="This interview is frozen to a different prompt version.",
        )
    if interview.setup_snapshot is None:
        scorecard = await database.get(Scorecard, interview.scorecard_id)
        profile = await database.get(CandidateProfile, interview.profile_id)
        if scorecard is None or profile is None:
            raise HTTPException(
                status_code=409, detail="The saved setup is incomplete."
            )
        job_target = await database.get(JobTarget, scorecard.job_target_id)
        if job_target is None:
            raise HTTPException(
                status_code=409, detail="The saved target role is missing."
            )
        interview.setup_snapshot = build_setup_snapshot(
            interview, profile, scorecard, job_target
        )
        interview.setup_fingerprint = setup_fingerprint(interview.setup_snapshot)
    elif interview.setup_fingerprint != setup_fingerprint(interview.setup_snapshot):
        raise HTTPException(
            status_code=409,
            detail="The frozen interview setup failed its integrity check.",
        )
    instructions = build_interview_prompt_from_snapshot(
        interview.setup_snapshot, prior_turns
    )
    await _enforce_secret_rate_limit(
        request,
        database,
        user_id=user.id,
        interview_id=interview.id,
    )
    try:
        secret = await create_realtime_client_secret(
            settings=settings,
            instructions=instructions,
            input_mode=payload.input_mode,
        )
    except RealtimeServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    interview.status = "RECONNECTING" if interview.started_at else "CONNECTING"
    database.add(
        UsageEvent(
            user_id=user.id,
            session_id=interview.id,
            kind="realtime_secret_created",
            quantity=1,
            estimated_cost_microusd=0,
        )
    )
    await database.commit()
    response = RealtimeClientSecretResponse(
        client_secret=secret.value,
        expires_at=secret.expires_at,
        calls_url=secret.calls_url,
        input_mode=payload.input_mode,
        prompt_version=PROMPT_VERSION,
    )
    request.app.state.realtime_secret_cache[interview.id] = {
        "key": cache_key,
        "response": response,
    }
    return response


@router.post(
    "/{interview_id}/connection-state",
    response_model=InterviewRuntimeResponse,
)
async def update_connection_state(
    interview_id: str,
    payload: ConnectionStateRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> InterviewRuntimeResponse:
    interview = await _owned_interview(database, user, interview_id, for_update=True)
    await _finalize_if_expired(interview, database)
    if interview.ended_at is not None:
        raise HTTPException(status_code=409, detail="This interview has already ended.")
    if interview.status not in CONNECTION_TRANSITIONS[payload.state]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Connection state '{payload.state}' is invalid while the interview "
                f"is {interview.status}."
            ),
        )
    now = datetime.now(UTC)
    if payload.state == "connected":
        if interview.started_at is None:
            interview.started_at = now
        interview.last_connected_at = now
        interview.recovery_started_at = None
        interview.status = "IN_PROGRESS"
    elif payload.state == "reconnecting":
        if interview.recovery_started_at is None:
            interview.recovery_started_at = now
        interview.status = "RECONNECTING"
    else:
        if interview.recovery_started_at is None:
            interview.recovery_started_at = now
        interview.status = "FAILED_RECOVERABLE"
    await database.commit()
    return _runtime_response(
        interview, await _turns(database, interview.id), request.app.state.settings
    )


@router.get("/{interview_id}/runtime", response_model=InterviewRuntimeResponse)
async def interview_runtime(
    interview_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> InterviewRuntimeResponse:
    interview = await _owned_interview(database, user, interview_id)
    await _finalize_if_expired(interview, database)
    return _runtime_response(
        interview, await _turns(database, interview.id), request.app.state.settings
    )


@router.post("/{interview_id}/turns:batch", response_model=InterviewRuntimeResponse)
async def upsert_interview_turns(
    interview_id: str,
    payload: InterviewTurnBatchRequest,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> InterviewRuntimeResponse:
    settings: Settings = request.app.state.settings
    interview = await _owned_interview(database, user, interview_id, for_update=True)
    expired = await _finalize_if_expired(interview, database)
    if expired:
        raise HTTPException(status_code=409, detail="The interview timer has expired.")
    if interview.status not in ACTIVE_INTERVIEW_STATES | {"TRANSCRIPT_FINALIZING"}:
        raise HTTPException(status_code=409, detail="The interview is not active.")
    if interview.ended_at is not None:
        client_ids = {item.client_turn_id for item in payload.items}
        existing_ids = set(
            await database.scalars(
                select(InterviewTurn.client_turn_id).where(
                    InterviewTurn.session_id == interview.id,
                    InterviewTurn.client_turn_id.in_(client_ids),
                )
            )
        )
        if existing_ids != client_ids:
            raise HTTPException(
                status_code=409,
                detail="New transcript turns are not accepted after the timer ends.",
            )
    maximum_sequence = await database.scalar(
        select(func.max(InterviewTurn.sequence)).where(
            InterviewTurn.session_id == interview.id
        )
    )
    next_sequence = (maximum_sequence or 0) + 1
    for item in payload.items:
        if (
            item.speaker == "user"
            and len(item.transcript) > settings.typed_answer_max_characters
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Typed answers are limited to "
                    f"{settings.typed_answer_max_characters:,} characters."
                ),
            )
        existing = await database.scalar(
            select(InterviewTurn).where(
                InterviewTurn.session_id == interview.id,
                InterviewTurn.client_turn_id == item.client_turn_id,
            )
        )
        if existing is not None:
            if (
                existing.speaker != item.speaker
                or existing.transcript != item.transcript
            ):
                raise HTTPException(
                    status_code=409,
                    detail="A turn ID cannot be reused with different content.",
                )
            if item.delivery_status == "acknowledged":
                existing.delivery_status = "acknowledged"
            if item.ended_at:
                existing.ended_at = item.ended_at
            continue
        database.add(
            InterviewTurn(
                session_id=interview.id,
                client_turn_id=item.client_turn_id,
                sequence=next_sequence,
                speaker=item.speaker,
                transcript=item.transcript,
                delivery_status=item.delivery_status,
                started_at=item.started_at or datetime.now(UTC),
                ended_at=item.ended_at,
            )
        )
        next_sequence += 1
    await database.commit()
    return _runtime_response(interview, await _turns(database, interview.id), settings)


@router.post("/{interview_id}/complete", response_model=InterviewRuntimeResponse)
async def complete_interview(
    interview_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> InterviewRuntimeResponse:
    interview = await _owned_interview(database, user, interview_id)
    await _finalize_if_expired(interview, database)
    if interview.ended_at is not None and interview.status in {
        "TRANSCRIPT_FINALIZING",
        "EVALUATING",
        "REPORT_READY",
        "FAILED_RECOVERABLE",
    }:
        if interview.status == "TRANSCRIPT_FINALIZING":
            background_tasks.add_task(run_evaluation_job, request.app, interview.id)
        return _runtime_response(
            interview,
            await _turns(database, interview.id),
            request.app.state.settings,
        )
    if interview.status not in ACTIVE_INTERVIEW_STATES | {"TRANSCRIPT_FINALIZING"}:
        raise HTTPException(status_code=409, detail="The interview is not active.")
    if interview.ended_at is None:
        interview.ended_at = datetime.now(UTC)
        interview.status = "TRANSCRIPT_FINALIZING"
        interview.recovery_started_at = None
        await database.commit()
    background_tasks.add_task(run_evaluation_job, request.app, interview.id)
    return _runtime_response(
        interview, await _turns(database, interview.id), request.app.state.settings
    )
