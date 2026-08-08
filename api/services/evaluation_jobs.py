"""Idempotent persistence orchestration for finalized interview evaluations."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from domain.evaluation import EvaluationReport, EvaluationTranscriptTurn
from domain.intake import ScorecardDocument, Seniority
from prompts.evaluation_v1 import PROMPT_VERSION as EVALUATOR_PROMPT_VERSION
from prompts.interview_v1 import setup_fingerprint

from ..models import Evaluation, InterviewSession, InterviewTurn, UsageEvent
from .evaluation import EvaluationServiceError, evaluate_transcript

logger = logging.getLogger(__name__)

EVALUATION_SCHEMA_VERSION = "evidence-report-v1"


async def _commit_unless_claimed(database: Any, interview_id: str) -> bool:
    """Commit this job's claim, yielding to whichever job inserted first.

    The selects above take ``FOR UPDATE``, which serializes concurrent jobs on
    PostgreSQL but is silently ignored by SQLite. There the guard cannot hold,
    so two duplicate requests both observe "no evaluation yet", both insert, and
    the second violates the unique constraint on ``evaluations.session_id``.
    Treat that violation as the intended outcome — one evaluation exists and
    another job owns it — rather than as an unhandled failure.
    """

    try:
        await database.commit()
        return True
    except IntegrityError:
        await database.rollback()
        logger.info(
            "evaluation_already_claimed",
            extra={"interview_id": interview_id},
        )
        return False


def transcript_fingerprint(turns: list[InterviewTurn]) -> str:
    """Fingerprint the exact ordered transcript boundary used for evaluation."""

    payload = [
        {
            "id": turn.id,
            "sequence": turn.sequence,
            "speaker": turn.speaker,
            "transcript": turn.transcript,
            "delivery_status": turn.delivery_status,
            "started_at": _iso(turn.started_at),
            "ended_at": _iso(turn.ended_at),
        }
        for turn in turns
    ]
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def run_evaluation_job(
    application: Any,
    interview_id: str,
    *,
    force_retry: bool = False,
) -> None:
    """Evaluate one interview without allowing duplicate or stale writes."""

    session_factory = application.state.session_factory
    settings = application.state.settings
    run_token = str(uuid.uuid4())
    report_input: (
        tuple[
            ScorecardDocument,
            Seniority,
            list[EvaluationTranscriptTurn],
            list[dict[str, object]],
            str,
        ]
        | None
    ) = None

    async with session_factory() as database:
        interview = await database.scalar(
            select(InterviewSession)
            .where(InterviewSession.id == interview_id)
            .with_for_update()
        )
        if interview is None:
            return
        existing = await database.scalar(
            select(Evaluation)
            .where(Evaluation.session_id == interview_id)
            .with_for_update()
        )
        if existing is not None and existing.status == "REPORT_READY":
            return
        if existing is not None and existing.status == "EVALUATING" and not force_retry:
            return
        try:
            (
                report_input,
                transcript_hash,
                setup_hash,
                persisted_turn_count,
            ) = await _finalized_input(
                database,
                interview,
                maximum_characters=settings.evaluation_max_transcript_characters,
            )
        except EvaluationServiceError as exc:
            evaluation = existing or _new_evaluation(interview)
            if existing is None:
                database.add(evaluation)
            _mark_failed(evaluation, _failure_code(exc))
            interview.status = "FAILED_RECOVERABLE"
            if not await _commit_unless_claimed(database, interview_id):
                return
            return

        now = datetime.now(UTC)
        evaluation = existing or _new_evaluation(interview)
        if existing is None:
            database.add(evaluation)
        evaluation.status = "EVALUATING"
        evaluation.schema_version = EVALUATION_SCHEMA_VERSION
        evaluation.evaluator_version = EVALUATOR_PROMPT_VERSION
        evaluation.prompt_version = interview.prompt_version or "unknown"
        evaluation.model_deployment = settings.azure_openai_text_deployment
        evaluation.setup_fingerprint = setup_hash
        evaluation.transcript_fingerprint = transcript_hash
        evaluation.transcript_turn_count = persisted_turn_count
        evaluation.transcript_finalized_at = interview.ended_at or now
        evaluation.scorecard_snapshot = report_input[0].model_dump(mode="json")
        evaluation.attempt_count = 0
        evaluation.run_token = run_token
        evaluation.started_at = now
        evaluation.failure_code = None
        interview.status = "EVALUATING"
        if not await _commit_unless_claimed(database, interview_id):
            return

    assert report_input is not None
    try:
        report = await evaluate_transcript(
            scorecard=report_input[0],
            seniority=report_input[1],
            turns=report_input[2],
            interview_section_timings=report_input[3],
            interview_prompt_version=report_input[4],
            settings=settings,
            timeout_seconds=settings.evaluation_llm_timeout_seconds,
        )
    except EvaluationServiceError as exc:
        async with session_factory() as database:
            await _persist_failure(database, interview_id, run_token, exc)
        return
    except Exception as exc:  # provider details must never be logged
        logger.warning(
            "evaluation_job_failed",
            extra={"interview_id": interview_id, "error_type": type(exc).__name__},
        )
        async with session_factory() as database:
            await _persist_failure(
                database,
                interview_id,
                run_token,
                EvaluationServiceError("Evaluation failed safely."),
            )
        return

    async with session_factory() as database:
        evaluation = await database.scalar(
            select(Evaluation)
            .where(Evaluation.session_id == interview_id)
            .with_for_update()
        )
        interview = await database.get(InterviewSession, interview_id)
        if evaluation is None or interview is None or evaluation.run_token != run_token:
            return
        _persist_report(evaluation, report)
        evaluation.status = "REPORT_READY"
        evaluation.completed_at = datetime.now(UTC)
        evaluation.failure_code = None
        interview.status = "REPORT_READY"
        database.add(
            UsageEvent(
                user_id=interview.user_id,
                session_id=interview.id,
                kind="evaluation_completed",
                quantity=1,
                estimated_cost_microusd=0,
            )
        )
        await database.commit()


async def _finalized_input(
    database: Any,
    interview: InterviewSession,
    *,
    maximum_characters: int,
) -> tuple[
    tuple[
        ScorecardDocument,
        Seniority,
        list[EvaluationTranscriptTurn],
        list[dict[str, object]],
        str,
    ],
    str,
    str,
    int,
]:
    if interview.ended_at is None or interview.setup_snapshot is None:
        raise EvaluationServiceError(
            "The interview is not finalized for evaluation.", status_code=409
        )
    setup_hash = setup_fingerprint(interview.setup_snapshot)
    if not interview.setup_fingerprint or setup_hash != interview.setup_fingerprint:
        raise EvaluationServiceError(
            "The frozen interview setup failed its integrity check.", status_code=409
        )
    result = await database.scalars(
        select(InterviewTurn)
        .where(InterviewTurn.session_id == interview.id)
        .order_by(InterviewTurn.sequence)
    )
    persisted_turns = list(result)
    if not any(turn.speaker == "user" for turn in persisted_turns):
        raise EvaluationServiceError(
            "The finalized transcript does not contain a candidate answer.",
            status_code=409,
        )
    if interview.input_mode == "voice" and any(
        turn.speaker == "user"
        and turn.transcription_source != "legacy"
        and turn.transcription_finalized_at is None
        for turn in persisted_turns
    ):
        raise EvaluationServiceError(
            "The transcript still has a candidate answer awaiting transcription "
            "finalization. Retry final transcription or accept the live fallback.",
            status_code=409,
        )
    if any(
        turn.speaker == "user" and turn.delivery_status != "acknowledged"
        for turn in persisted_turns
    ):
        raise EvaluationServiceError(
            "The transcript still has an unconfirmed candidate answer. Reconnect "
            "once, then retry evaluation.",
            status_code=409,
        )
    if sum(len(turn.transcript) for turn in persisted_turns) > maximum_characters:
        raise EvaluationServiceError(
            "The finalized transcript is too large to evaluate safely.", status_code=409
        )
    turns = [
        EvaluationTranscriptTurn(
            id=turn.id,
            sequence=turn.sequence,
            speaker=turn.speaker,
            transcript=turn.transcript,
            delivery_status=turn.delivery_status,
        )
        for turn in persisted_turns
        if turn.delivery_status == "acknowledged"
    ]
    snapshot = interview.setup_snapshot
    scorecard_payload = dict(snapshot.get("scorecard", {}))
    competencies = []
    for raw in scorecard_payload.get("competencies", []):
        item = dict(raw)
        if "seniority_expectation" not in item and "expectation" in item:
            item["seniority_expectation"] = item.pop("expectation")
        item.setdefault(
            "source_references",
            [
                {
                    "source_id": "frozen-scorecard",
                    "label": "Frozen reviewed scorecard",
                    "excerpt": str(item.get("description", "Role requirement")),
                }
            ],
        )
        competencies.append(item)
    try:
        scorecard = ScorecardDocument.model_validate(
            {
                "competencies": competencies,
                "generator_version": "frozen-scorecard",
            }
        )
        target = dict(snapshot.get("target", {}))
        seniority: Seniority = target["seniority"]  # type: ignore[assignment]
        if seniority not in {"junior", "mid", "senior"}:
            raise ValueError("invalid seniority")
        section_plan = [dict(item) for item in snapshot.get("section_plan", [])]
    except (KeyError, TypeError, ValueError) as exc:
        raise EvaluationServiceError(
            "The frozen interview setup is not valid for evaluation.", status_code=409
        ) from exc
    return (
        (
            scorecard,
            seniority,
            turns,
            section_plan,
            interview.prompt_version or "unknown",
        ),
        transcript_fingerprint(persisted_turns),
        setup_hash,
        len(persisted_turns),
    )


def _new_evaluation(interview: InterviewSession) -> Evaluation:
    now = datetime.now(UTC)
    return Evaluation(
        session_id=interview.id,
        status="PENDING",
        schema_version=EVALUATION_SCHEMA_VERSION,
        evaluator_version=EVALUATOR_PROMPT_VERSION,
        prompt_version=interview.prompt_version or "unknown",
        model_deployment="",
        setup_fingerprint=interview.setup_fingerprint or "",
        transcript_fingerprint="",
        transcript_turn_count=0,
        transcript_finalized_at=interview.ended_at or now,
        scorecard_snapshot={},
        attempt_count=0,
        competency_results=[],
        overall_result=None,
        strengths=[],
        gaps=[],
        practice_exercises=[],
        uncertainty=[],
    )


def _persist_report(evaluation: Evaluation, report: EvaluationReport) -> None:
    evaluation.evaluator_version = report.evaluator_version
    evaluation.attempt_count = report.validation_attempts
    evaluation.competency_results = [
        item.model_dump(mode="json") for item in report.competency_results
    ]
    evaluation.overall_result = {
        "score": report.overall_score,
        "assessed_weight": report.assessed_weight,
        "total_weight": report.total_weight,
        "coverage_percentage": report.coverage_percentage,
        "evidence_locations": [
            item.model_dump(mode="json") for item in report.evidence_locations
        ],
    }
    evaluation.strengths = report.strength_competency_ids
    evaluation.gaps = report.gap_competency_ids
    evaluation.practice_exercises = [
        item.model_dump(mode="json") for item in report.practice_exercises
    ]
    evaluation.uncertainty = [
        item.competency_id
        for item in report.competency_results
        if item.assessment == "not_assessed" or item.rating_confidence == "low"
    ]


async def _persist_failure(
    database: Any,
    interview_id: str,
    run_token: str,
    error: EvaluationServiceError,
) -> None:
    evaluation = await database.scalar(
        select(Evaluation)
        .where(Evaluation.session_id == interview_id)
        .with_for_update()
    )
    interview = await database.get(InterviewSession, interview_id)
    if evaluation is None or interview is None or evaluation.run_token != run_token:
        return
    evaluation.attempt_count = 2 if error.integrity_issues else 1
    _mark_failed(evaluation, _failure_code(error))
    interview.status = "FAILED_RECOVERABLE"
    await database.commit()


def _mark_failed(evaluation: Evaluation, failure_code: str) -> None:
    evaluation.status = "FAILED_RECOVERABLE"
    evaluation.failure_code = failure_code
    evaluation.run_token = None
    evaluation.competency_results = []
    evaluation.overall_result = None
    evaluation.strengths = []
    evaluation.gaps = []
    evaluation.practice_exercises = []
    evaluation.uncertainty = []


def _failure_code(error: EvaluationServiceError) -> str:
    if error.integrity_issues:
        return "unsupported_evidence"
    if error.status_code == 504:
        return "provider_timeout"
    if error.status_code == 503:
        return "provider_not_configured"
    if error.status_code == 409:
        return "transcript_not_ready"
    return "provider_unavailable"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()
