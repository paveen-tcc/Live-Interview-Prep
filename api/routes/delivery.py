"""Consent-gated, observable speaking-delivery coaching endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.delivery import (
    DeliveryBaseline,
    DeliveryObservation,
    DeliveryTurnMetric,
    build_observations,
    build_suggestions,
    calculate_turn_metric,
    establish_baseline,
)

from ..auth import get_current_user
from ..database import get_database_session
from ..delivery_schemas import (
    DeliveryCoachingResponse,
    DeliveryConsentRequest,
    DeliveryObservationBatchRequest,
)
from ..models import DeliveryCoachingRecord, InterviewSession, InterviewTurn, User

router = APIRouter(prefix="/api/interviews", tags=["delivery coaching"])


async def _owned_interview(
    database: AsyncSession, user: User, interview_id: str
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


async def _record(
    database: AsyncSession, interview_id: str, *, create: bool = False
) -> DeliveryCoachingRecord | None:
    record = await database.scalar(
        select(DeliveryCoachingRecord).where(
            DeliveryCoachingRecord.session_id == interview_id
        )
    )
    if record is None and create:
        record = DeliveryCoachingRecord(
            session_id=interview_id,
            consented=False,
            speech_observations=[],
            metrics=[],
            baseline=None,
            observations=[],
            suggestions=[],
        )
        database.add(record)
    return record


@router.post(
    "/{interview_id}/delivery-consent", response_model=DeliveryCoachingResponse
)
async def update_delivery_consent(
    interview_id: str,
    payload: DeliveryConsentRequest,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> DeliveryCoachingResponse:
    interview = await _owned_interview(database, user, interview_id)
    if payload.enabled and interview.input_mode == "text_dev":
        raise HTTPException(
            status_code=409,
            detail="Speaking-delivery coaching is unavailable in developer text mode.",
        )
    record = await _record(database, interview.id, create=True)
    assert record is not None
    now = datetime.now(UTC)
    record.consented = payload.enabled
    record.consent_version = payload.consent_version
    if payload.enabled:
        record.consented_at = now
        record.disabled_at = None
        record.deleted_at = None
    else:
        record.disabled_at = now
    await database.commit()
    return delivery_coaching_response(interview, record)


@router.post(
    "/{interview_id}/delivery-observations",
    response_model=DeliveryCoachingResponse,
)
async def save_delivery_observations(
    interview_id: str,
    payload: DeliveryObservationBatchRequest,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> DeliveryCoachingResponse:
    interview = await _owned_interview(database, user, interview_id)
    record = await _record(database, interview.id)
    if interview.input_mode != "voice" or record is None or not record.consented:
        raise HTTPException(
            status_code=409,
            detail="Enable speaking-delivery coaching before saving observations.",
        )
    turns = list(
        await database.scalars(
            select(InterviewTurn)
            .where(InterviewTurn.session_id == interview.id)
            .order_by(InterviewTurn.sequence)
        )
    )
    turn_by_id = {turn.id: turn for turn in turns}
    raw_by_turn = {
        str(item["turn_id"]): item for item in (record.speech_observations or [])
    }
    for item in payload.items:
        turn = turn_by_id.get(item.turn_id)
        if (
            turn is None
            or turn.speaker != "user"
            or turn.delivery_status != "acknowledged"
        ):
            raise HTTPException(
                status_code=422,
                detail="Delivery observations require an acknowledged candidate turn.",
            )
        ordered = sorted(item.speech_segments, key=lambda value: value.started_at)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current.started_at < previous.ended_at:
                raise HTTPException(
                    status_code=422, detail="Speech segments cannot overlap."
                )
        raw_by_turn[item.turn_id] = {
            "turn_id": item.turn_id,
            "speech_segments": [segment.model_dump(mode="json") for segment in ordered],
        }
    record.speech_observations = list(raw_by_turn.values())
    metrics = _calculate_metrics(turns, record.speech_observations)
    baseline = establish_baseline(metrics)
    observations = build_observations(metrics, baseline)
    record.metrics = [item.model_dump(mode="json") for item in metrics]
    record.baseline = baseline.model_dump(mode="json") if baseline else None
    record.observations = [item.model_dump(mode="json") for item in observations]
    record.suggestions = build_suggestions(metrics, baseline)
    await database.commit()
    return delivery_coaching_response(interview, record)


@router.get(
    "/{interview_id}/delivery-coaching", response_model=DeliveryCoachingResponse
)
async def get_delivery_coaching(
    interview_id: str,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> DeliveryCoachingResponse:
    interview = await _owned_interview(database, user, interview_id)
    return delivery_coaching_response(interview, await _record(database, interview.id))


@router.delete(
    "/{interview_id}/delivery-metrics", response_model=DeliveryCoachingResponse
)
async def delete_delivery_metrics(
    interview_id: str,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> DeliveryCoachingResponse:
    interview = await _owned_interview(database, user, interview_id)
    record = await _record(database, interview.id, create=True)
    assert record is not None
    record.consented = False
    record.deleted_at = datetime.now(UTC)
    record.speech_observations = []
    record.metrics = []
    record.baseline = None
    record.observations = []
    record.suggestions = []
    await database.commit()
    return delivery_coaching_response(interview, record)


def delivery_coaching_response(
    interview: InterviewSession, record: DeliveryCoachingRecord | None
) -> DeliveryCoachingResponse:
    if interview.input_mode == "text_dev":
        status = "unavailable"
        unavailable_reason = "text_input_mode"
    elif record is not None and record.deleted_at is not None:
        status = "deleted"
        unavailable_reason = None
    elif record is not None and not record.consented and record.disabled_at is not None:
        status = "disabled"
        unavailable_reason = None
    elif record is None or not record.consented:
        status = "unavailable"
        unavailable_reason = "consent_required"
    elif record.metrics:
        status = "available"
        unavailable_reason = None
    else:
        status = "collecting"
        unavailable_reason = "no_observations"
    return DeliveryCoachingResponse(
        interview_id=interview.id,
        status=status,
        consented=bool(record and record.consented),
        consent_version=record.consent_version if record else None,
        unavailable_reason=unavailable_reason,
        baseline=(
            DeliveryBaseline.model_validate(record.baseline)
            if record and record.baseline
            else None
        ),
        metrics=[
            DeliveryTurnMetric.model_validate(item)
            for item in (record.metrics if record else [])
        ],
        observations=[
            DeliveryObservation.model_validate(item)
            for item in (record.observations if record else [])
        ],
        suggestions=list(record.suggestions if record else []),
    )


async def load_delivery_coaching(
    database: AsyncSession, interview: InterviewSession
) -> DeliveryCoachingResponse:
    """Load the report-safe delivery dimension without touching role-fit scores."""

    return delivery_coaching_response(interview, await _record(database, interview.id))


def _calculate_metrics(
    turns: list[InterviewTurn], raw_observations: list[dict[str, object]]
) -> list[DeliveryTurnMetric]:
    turn_by_id = {turn.id: turn for turn in turns}
    metrics: list[DeliveryTurnMetric] = []
    for raw in raw_observations:
        turn = turn_by_id.get(str(raw["turn_id"]))
        if turn is None:
            continue
        segments = list(raw["speech_segments"])
        absolute = [
            (
                datetime.fromisoformat(str(item["started_at"])),
                datetime.fromisoformat(str(item["ended_at"])),
            )
            for item in segments
        ]
        origin = absolute[0][0]
        relative = [
            (
                round((started - origin).total_seconds() * 1_000),
                round((ended - origin).total_seconds() * 1_000),
            )
            for started, ended in absolute
        ]
        prior_assistant = next(
            (
                candidate
                for candidate in reversed(turns)
                if candidate.sequence < turn.sequence
                and candidate.speaker == "assistant"
                and candidate.ended_at is not None
            ),
            None,
        )
        response_delay_ms = None
        if prior_assistant and prior_assistant.ended_at:
            assistant_end = _ensure_utc(prior_assistant.ended_at)
            response_delay_ms = max(
                0, round((absolute[0][0] - assistant_end).total_seconds() * 1_000)
            )
        interruption_count = sum(
            1
            for candidate in turns
            if candidate.speaker == "assistant"
            and candidate.started_at is not None
            and candidate.ended_at is not None
            and any(
                started < _ensure_utc(candidate.ended_at)
                and ended > _ensure_utc(candidate.started_at)
                for started, ended in absolute
            )
        )
        metrics.append(
            calculate_turn_metric(
                turn_id=turn.id,
                sequence=turn.sequence,
                transcript=turn.transcript,
                speech_segments_ms=relative,
                response_delay_ms=response_delay_ms,
                interruption_count=interruption_count,
            )
        )
    return sorted(metrics, key=lambda item: item.sequence)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
