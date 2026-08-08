"""Owned M3 Realtime session, recovery, and transcript endpoints."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
)
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
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
    ClientTurnId,
    ConnectionStateRequest,
    InterviewRuntimeResponse,
    InterviewTurnBatchRequest,
    InterviewTurnResponse,
    RealtimeClientSecretRequest,
    RealtimeClientSecretResponse,
    TranscriptionEventRequest,
)
from ..services.audio_multipart import (
    AudioMultipartError,
    CandidateAudioMultipart,
    parse_candidate_audio_multipart,
)
from ..services.evaluation_jobs import run_evaluation_job
from ..services.realtime import RealtimeServiceError, create_realtime_client_secret
from ..services.transcription import (
    FinalTranscription,
    TranscriptionServiceError,
    build_transcription_prompt,
    transcribe_candidate_audio,
)

router = APIRouter(prefix="/api/interviews", tags=["realtime"])
logger = logging.getLogger(__name__)

_ALLOWED_TRANSCRIPTION_MEDIA_TYPES = {"audio/webm", "audio/mp4", "audio/ogg"}


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
    return await _owned_interview_for_user_id(
        database,
        user.id,
        interview_id,
        for_update=for_update,
    )


async def _owned_interview_for_user_id(
    database: AsyncSession,
    user_id: str,
    interview_id: str,
    *,
    for_update: bool = False,
) -> InterviewSession:
    statement = select(InterviewSession).where(
        InterviewSession.id == interview_id,
        InterviewSession.user_id == user_id,
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


async def _turn_by_client_id(
    database: AsyncSession,
    interview_id: str,
    client_turn_id: str,
    *,
    for_update: bool = False,
) -> InterviewTurn | None:
    statement = select(InterviewTurn).where(
        InterviewTurn.session_id == interview_id,
        InterviewTurn.client_turn_id == client_turn_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return await database.scalar(statement)


def _turn_response(turn: InterviewTurn) -> InterviewTurnResponse:
    return InterviewTurnResponse(
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


def _latency_bucket(elapsed_ms: int) -> str:
    if elapsed_ms < 1_000:
        return "under_1s"
    if elapsed_ms < 3_000:
        return "1s_to_3s"
    if elapsed_ms < 10_000:
        return "3s_to_10s"
    return "over_10s"


def _retryable_turn_write(error: Exception) -> bool:
    if isinstance(error, IntegrityError):
        return True
    if not isinstance(error, OperationalError):
        return False
    message = str(error.orig).lower()
    return "database is locked" in message or "database table is locked" in message


async def _retry_turn_write(attempt: int) -> None:
    await asyncio.sleep(0.01 * (2**attempt))


def _runtime_response(
    interview: InterviewSession,
    turns: list[InterviewTurn],
    settings: Settings,
) -> InterviewRuntimeResponse:
    now = datetime.now(UTC)
    ends_at = _scheduled_end(interview)
    normalized_turns = [_turn_response(turn) for turn in turns]
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


async def _runtime_for_user_id(
    database: AsyncSession,
    user_id: str,
    interview_id: str,
    settings: Settings,
) -> InterviewRuntimeResponse:
    interview = await _owned_interview_for_user_id(database, user_id, interview_id)
    return _runtime_response(interview, await _turns(database, interview.id), settings)


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
    user_id = user.id
    for attempt in range(3):
        try:
            interview = await _upsert_interview_turns_once(
                database=database,
                user_id=user_id,
                interview_id=interview_id,
                payload=payload,
                settings=settings,
            )
            await database.commit()
            return _runtime_response(
                interview, await _turns(database, interview.id), settings
            )
        except (IntegrityError, OperationalError) as exc:
            await database.rollback()
            if not _retryable_turn_write(exc) or attempt == 2:
                raise HTTPException(
                    status_code=409,
                    detail="Transcript turns changed concurrently. Retry the request.",
                ) from exc
            await _retry_turn_write(attempt)
    raise AssertionError("turn persistence retry loop did not return or raise")


async def _upsert_interview_turns_once(
    *,
    database: AsyncSession,
    user_id: str,
    interview_id: str,
    payload: InterviewTurnBatchRequest,
    settings: Settings,
) -> InterviewSession:
    interview = await _owned_interview_for_user_id(
        database, user_id, interview_id, for_update=True
    )
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
        existing = await _turn_by_client_id(
            database,
            interview.id,
            item.client_turn_id,
            for_update=True,
        )
        if existing is not None:
            if existing.speaker != item.speaker:
                raise HTTPException(
                    status_code=409,
                    detail="A turn ID cannot be reused with different content.",
                )
            if existing.transcript != item.transcript:
                if (
                    existing.transcription_source == "final_model"
                    and item.speaker == "user"
                    and interview.input_mode == "voice"
                ):
                    continue
                raise HTTPException(
                    status_code=409,
                    detail="A turn ID cannot be reused with different content.",
                )
            if item.delivery_status == "acknowledged":
                existing.delivery_status = "acknowledged"
            if item.ended_at:
                existing.ended_at = item.ended_at
            continue
        source = (
            "assistant"
            if item.speaker == "assistant"
            else "typed"
            if interview.input_mode == "text_dev"
            else "realtime_live"
        )
        database.add(
            InterviewTurn(
                session_id=interview.id,
                client_turn_id=item.client_turn_id,
                sequence=next_sequence,
                speaker=item.speaker,
                transcript=item.transcript,
                transcription_source=source,
                transcription_model=(
                    settings.azure_openai_realtime_transcription_model
                    if source == "realtime_live"
                    else None
                ),
                transcription_finalized_at=(
                    datetime.now(UTC) if source in {"assistant", "typed"} else None
                ),
                delivery_status=item.delivery_status,
                started_at=item.started_at or datetime.now(UTC),
                ended_at=item.ended_at,
            )
        )
        next_sequence += 1
    return interview


@router.post(
    "/{interview_id}/turns/{client_turn_id}:transcribe",
    response_model=InterviewRuntimeResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "started_at": {
                                "type": "string",
                                "format": "date-time",
                            },
                            "ended_at": {
                                "type": "string",
                                "format": "date-time",
                            },
                        },
                    }
                }
            },
        }
    },
)
async def finalize_candidate_transcription(
    interview_id: str,
    client_turn_id: ClientTurnId,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> InterviewRuntimeResponse:
    settings: Settings = request.app.state.settings
    started = time.perf_counter()
    user_id = user.id
    interview = await _owned_interview_for_user_id(
        database, user_id, interview_id, for_update=True
    )
    await _finalize_if_expired(interview, database)
    existing = await _turn_by_client_id(
        database, interview.id, client_turn_id, for_update=True
    )
    if existing is not None and existing.transcription_source == "final_model":
        return _runtime_response(
            interview, await _turns(database, interview.id), settings
        )
    if interview.input_mode != "voice":
        raise HTTPException(
            status_code=409,
            detail="Final audio transcription is available only in voice mode.",
        )
    if existing is not None and (
        existing.speaker != "user" or existing.transcription_source != "realtime_live"
    ):
        raise HTTPException(
            status_code=409,
            detail="Only a live candidate turn can be finalized from audio.",
        )
    if interview.ended_at is not None and existing is None:
        raise HTTPException(
            status_code=409,
            detail="New transcript turns are not accepted after the timer ends.",
        )
    if existing is None and interview.status not in ACTIVE_INTERVIEW_STATES | {
        "TRANSCRIPT_FINALIZING"
    }:
        raise HTTPException(status_code=409, detail="The interview is not active.")

    content_length_header = request.headers.get("content-length")
    try:
        content_length = (
            int(content_length_header) if content_length_header is not None else None
        )
    except ValueError:
        content_length = None
    try:
        upload = await parse_candidate_audio_multipart(
            chunks=request.stream(),
            content_type=request.headers.get("content-type", ""),
            content_length=content_length,
            max_audio_bytes=settings.azure_openai_final_transcription_max_bytes,
        )
    except AudioMultipartError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if upload.media_type not in _ALLOWED_TRANSCRIPTION_MEDIA_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Candidate audio must be WebM, MP4, or Ogg.",
        )
    prompt = build_transcription_prompt(interview.setup_snapshot or {})
    await database.rollback()

    try:
        result = await transcribe_candidate_audio(
            settings=settings,
            audio=upload.audio,
            media_type=upload.media_type,
            filename=upload.filename or f"{client_turn_id}.webm",
            prompt=prompt,
        )
    except TranscriptionServiceError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1_000)
        logger.warning(
            "final_transcription_failed",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "interview_id": interview_id,
                "safe_error_code": exc.code,
                "attempt_count": exc.attempts,
                "latency_bucket": _latency_bucket(elapsed_ms),
            },
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1_000)
        logger.warning(
            "final_transcription_failed",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "interview_id": interview_id,
                "safe_error_code": "transcription_unexpected",
                "attempt_count": 1,
                "latency_bucket": _latency_bucket(elapsed_ms),
            },
        )
        raise HTTPException(
            status_code=502,
            detail="Final transcription failed safely. Try again.",
        ) from exc

    for attempt in range(3):
        try:
            _turn, wrote_final = await _persist_final_transcription_once(
                database=database,
                user_id=user_id,
                interview_id=interview_id,
                client_turn_id=client_turn_id,
                upload=upload,
                result=result,
            )
            await database.commit()
            if wrote_final:
                logger.info(
                    "final_transcription_completed",
                    extra={
                        "request_id": getattr(request.state, "request_id", None),
                        "interview_id": interview_id,
                        "safe_error_code": None,
                        "attempt_count": result.attempts,
                        "latency_bucket": _latency_bucket(result.elapsed_ms),
                    },
                )
            return await _runtime_for_user_id(database, user_id, interview_id, settings)
        except (IntegrityError, OperationalError) as exc:
            await database.rollback()
            if not _retryable_turn_write(exc) or attempt == 2:
                raise HTTPException(
                    status_code=409,
                    detail="Candidate transcription changed concurrently. Retry.",
                ) from exc
            await _retry_turn_write(attempt)
    raise AssertionError("final transcription retry loop did not return or raise")


async def _persist_final_transcription_once(
    *,
    database: AsyncSession,
    user_id: str,
    interview_id: str,
    client_turn_id: str,
    upload: CandidateAudioMultipart,
    result: FinalTranscription,
) -> tuple[InterviewTurn, bool]:
    interview = await _owned_interview_for_user_id(
        database, user_id, interview_id, for_update=True
    )
    await _finalize_if_expired(interview, database)
    turn = await _turn_by_client_id(
        database, interview.id, client_turn_id, for_update=True
    )
    if turn is not None and turn.transcription_source == "final_model":
        return turn, False
    if turn is not None and (
        turn.speaker != "user" or turn.transcription_source != "realtime_live"
    ):
        raise HTTPException(
            status_code=409,
            detail="Only a live candidate turn can be finalized from audio.",
        )
    if interview.ended_at is not None and turn is None:
        raise HTTPException(
            status_code=409,
            detail="New transcript turns are not accepted after the timer ends.",
        )
    now = datetime.now(UTC)
    if turn is None:
        maximum_sequence = await database.scalar(
            select(func.max(InterviewTurn.sequence)).where(
                InterviewTurn.session_id == interview.id
            )
        )
        turn = InterviewTurn(
            session_id=interview.id,
            client_turn_id=client_turn_id,
            sequence=(maximum_sequence or 0) + 1,
            speaker="user",
            transcript=result.text,
            delivery_status="acknowledged",
            transcription_source="final_model",
            transcription_model=result.deployment,
            transcription_finalized_at=now,
            started_at=upload.started_at or now,
            ended_at=upload.ended_at,
        )
        database.add(turn)
    else:
        turn.transcript = result.text
        turn.delivery_status = "acknowledged"
        turn.transcription_source = "final_model"
        turn.transcription_model = result.deployment
        turn.transcription_finalized_at = now
        if upload.started_at is not None:
            turn.started_at = upload.started_at
        if upload.ended_at is not None:
            turn.ended_at = upload.ended_at
    database.add(
        UsageEvent(
            user_id=user_id,
            session_id=interview.id,
            kind="final_transcription_completed",
            quantity=1,
            estimated_cost_microusd=0,
        )
    )
    return turn, True


@router.post(
    "/{interview_id}/turns/{client_turn_id}:accept-live",
    response_model=InterviewRuntimeResponse,
)
async def accept_live_candidate_transcription(
    interview_id: str,
    client_turn_id: ClientTurnId,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> InterviewRuntimeResponse:
    user_id = user.id
    interview = await _owned_interview_for_user_id(
        database, user_id, interview_id, for_update=True
    )
    await _finalize_if_expired(interview, database)
    turn = await _turn_by_client_id(
        database, interview.id, client_turn_id, for_update=True
    )
    if turn is None:
        raise HTTPException(status_code=404, detail="Candidate turn was not found.")
    if turn.speaker == "user" and turn.transcription_source == "final_model":
        return _runtime_response(
            interview,
            await _turns(database, interview.id),
            request.app.state.settings,
        )
    if turn.speaker != "user" or turn.transcription_source != "realtime_live":
        raise HTTPException(
            status_code=409,
            detail="Only a live candidate transcript can be accepted as fallback.",
        )
    await database.rollback()
    for attempt in range(3):
        try:
            accepted_at = datetime.now(UTC)
            accepted = await database.execute(
                update(InterviewTurn)
                .where(
                    InterviewTurn.session_id == interview_id,
                    InterviewTurn.client_turn_id == client_turn_id,
                    InterviewTurn.speaker == "user",
                    InterviewTurn.transcription_source == "realtime_live",
                    InterviewTurn.transcription_finalized_at.is_(None),
                )
                .values(transcription_finalized_at=accepted_at)
            )
            if accepted.rowcount == 1:  # type: ignore[attr-defined]
                database.add(
                    UsageEvent(
                        user_id=user_id,
                        session_id=interview_id,
                        kind="live_transcription_fallback",
                        quantity=1,
                        estimated_cost_microusd=0,
                    )
                )
                await database.commit()
            else:
                await database.rollback()
            break
        except OperationalError as exc:
            await database.rollback()
            if not _retryable_turn_write(exc) or attempt == 2:
                raise HTTPException(
                    status_code=409,
                    detail="Live transcription changed concurrently. Retry.",
                ) from exc
            await _retry_turn_write(attempt)
    else:
        raise AssertionError("live acceptance retry loop did not finish")
    saved = await _turn_by_client_id(database, interview_id, client_turn_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Candidate turn was not found.")
    if saved.speaker != "user" or saved.transcription_source != "realtime_live":
        raise HTTPException(
            status_code=409,
            detail="Only a live candidate transcript can be accepted as fallback.",
        )
    return await _runtime_for_user_id(
        database,
        user_id,
        interview_id,
        request.app.state.settings,
    )


@router.post("/{interview_id}/transcription-events", status_code=204)
async def record_transcription_event(
    interview_id: str,
    payload: TranscriptionEventRequest,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> Response:
    interview = await _owned_interview(database, user, interview_id)
    database.add(
        UsageEvent(
            user_id=user.id,
            session_id=interview.id,
            kind=payload.kind,
            quantity=1,
            estimated_cost_microusd=0,
        )
    )
    await database.commit()
    return Response(status_code=204)


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
