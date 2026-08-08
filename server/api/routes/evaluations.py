"""Owned evidence-backed evaluation and report endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..database import get_database_session
from ..evaluation_schemas import (
    CompetencyReportResult,
    EvaluationStatusResponse,
    InterviewReportResponse,
    PracticeExercise,
    ReportEvidence,
)
from ..models import Evaluation, InterviewSession, InterviewTurn, UsageEvent, User
from ..services.evaluation_jobs import run_evaluation_job
from .delivery import load_delivery_coaching

router = APIRouter(prefix="/api/interviews", tags=["evaluation"])


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


@router.post("/{interview_id}/evaluate", response_model=EvaluationStatusResponse)
async def evaluate_interview(
    interview_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> EvaluationStatusResponse:
    interview = await _owned_interview(database, user, interview_id)
    if interview.ended_at is None:
        raise HTTPException(
            status_code=409, detail="Complete the interview before evaluation."
        )
    evaluation = await database.scalar(
        select(Evaluation).where(Evaluation.session_id == interview.id)
    )
    if evaluation is not None and evaluation.status == "REPORT_READY":
        return EvaluationStatusResponse(
            interview_id=interview.id, status="REPORT_READY"
        )
    if evaluation is None or evaluation.status != "EVALUATING":
        settings = request.app.state.settings
        now = datetime.now(UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        evaluation_count = int(
            await database.scalar(
                select(func.count())
                .select_from(UsageEvent)
                .where(
                    UsageEvent.user_id == user.id,
                    UsageEvent.kind == "evaluation_started",
                    UsageEvent.created_at >= day_start,
                )
            )
            or 0
        )
        if evaluation_count >= settings.daily_evaluation_quota:
            retry_after = max(
                1,
                round((day_start + timedelta(days=1) - now).total_seconds()),
            )
            raise HTTPException(
                status_code=429,
                detail=(
                    "Your private-alpha daily evaluation quota is used. "
                    "Retry after the UTC quota window resets."
                ),
                headers={"Retry-After": str(retry_after)},
            )
        database.add(
            UsageEvent(
                user_id=user.id,
                session_id=interview.id,
                kind="evaluation_started",
                quantity=1,
                estimated_cost_microusd=0,
                created_at=now,
            )
        )
        await database.commit()
        background_tasks.add_task(
            run_evaluation_job,
            request.app,
            interview.id,
            force_retry=evaluation is not None,
        )
    return EvaluationStatusResponse(interview_id=interview.id, status="EVALUATING")


@router.get("/{interview_id}/report")
async def interview_report(
    interview_id: str,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> EvaluationStatusResponse | InterviewReportResponse:
    interview = await _owned_interview(database, user, interview_id)
    evaluation = await database.scalar(
        select(Evaluation).where(Evaluation.session_id == interview.id)
    )
    if evaluation is None:
        return EvaluationStatusResponse(
            interview_id=interview.id,
            status=(
                "TRANSCRIPT_FINALIZING"
                if interview.ended_at is not None
                else interview.status
            ),
        )
    if evaluation.status != "REPORT_READY":
        return EvaluationStatusResponse(
            interview_id=interview.id, status=evaluation.status
        )
    if evaluation.completed_at is None or evaluation.overall_result is None:
        raise HTTPException(status_code=409, detail="The report is not ready yet.")

    scorecard = dict(evaluation.scorecard_snapshot)
    competency_lookup = {
        str(item["id"]): dict(item) for item in scorecard.get("competencies", [])
    }
    turns = list(
        await database.scalars(
            select(InterviewTurn)
            .where(InterviewTurn.session_id == interview.id)
            .order_by(InterviewTurn.sequence)
        )
    )
    turn_sequence = {turn.id: turn.sequence for turn in turns}
    competency_results: list[CompetencyReportResult] = []
    for raw_result in evaluation.competency_results:
        result = dict(raw_result)
        competency = competency_lookup.get(str(result.get("competency_id")))
        if competency is None:
            raise HTTPException(
                status_code=409,
                detail="The report failed its scorecard integrity check.",
            )
        competency_results.append(
            CompetencyReportResult(
                competency_id=str(result["competency_id"]),
                name=str(competency["name"]),
                weight=int(competency["weight"]),
                classification=competency["classification"],
                assessment=result["assessment"],
                score=result.get("score"),
                rating_confidence=result.get("rating_confidence"),
                evidence=[
                    ReportEvidence(
                        turn_id=str(item["turn_id"]),
                        sequence=turn_sequence[str(item["turn_id"])],
                        quote=str(item["quote"]),
                    )
                    for item in result.get("evidence", [])
                ],
                evidence_summary=result.get("evidence_summary"),
                gaps=[str(item) for item in result.get("gaps", [])],
                recommendations=[
                    str(item) for item in result.get("recommendations", [])
                ],
                not_assessed_reason=result.get("not_assessed_reason"),
            )
        )
    overall = dict(evaluation.overall_result)
    return InterviewReportResponse(
        interview_id=interview.id,
        status="REPORT_READY",
        evaluator_version=evaluation.evaluator_version,
        prompt_version=evaluation.prompt_version,
        overall_score=overall.get("score"),
        assessed_weight=int(overall["assessed_weight"]),
        total_weight=int(overall["total_weight"]),
        coverage_percentage=float(overall["coverage_percentage"]),
        competency_results=competency_results,
        strengths=_competency_names(evaluation.strengths, competency_lookup),
        gaps=_competency_names(evaluation.gaps, competency_lookup),
        practice_exercises=[
            PracticeExercise.model_validate(item)
            for item in evaluation.practice_exercises
        ],
        uncertainty=_competency_names(evaluation.uncertainty, competency_lookup),
        delivery_coaching=await load_delivery_coaching(database, interview),
        completed_at=evaluation.completed_at,
    )


def _competency_names(
    identifiers: list[str], lookup: dict[str, dict[str, object]]
) -> list[str]:
    return [str(lookup[item]["name"]) for item in identifiers if item in lookup]
