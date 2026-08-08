from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from api.config import Settings
from api.services.evaluation import EvaluationServiceError, evaluate_transcript
from domain.evaluation import (
    EvaluationDraft,
    EvaluationIntegrityError,
    EvaluationTranscriptTurn,
    EvidenceCitation,
    validate_and_score_evaluation,
)
from domain.intake import ScorecardDocument

GOLDEN_PATH = (
    Path(__file__).resolve().parents[1] / "evals" / "m4_backend_evidence_golden.json"
)


def _golden() -> dict[str, Any]:
    return json.loads(GOLDEN_PATH.read_text())


def _contracts() -> tuple[
    ScorecardDocument, list[EvaluationTranscriptTurn], EvaluationDraft
]:
    case = _golden()
    return (
        ScorecardDocument.model_validate(case["scorecard"]),
        [EvaluationTranscriptTurn.model_validate(item) for item in case["turns"]],
        EvaluationDraft.model_validate(case["valid_draft"]),
    )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_api_key="test-key",
        azure_openai_text_deployment="gpt-test",
    )


class FakeResponses:
    def __init__(self, outputs: list[EvaluationDraft | dict[str, object] | None]):
        self.outputs = outputs
        self.calls: list[dict[str, object]] = []

    async def parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.outputs.pop(0))


def test_golden_evaluation_validates_and_scores_only_assessed_weight() -> None:
    scorecard, turns, draft = _contracts()
    expected = _golden()["expected"]

    report = validate_and_score_evaluation(
        draft,
        scorecard,
        turns,
        evaluator_version="test-v1",
    )

    assert report.overall_score == expected["overall_score"]
    assert report.assessed_weight == expected["assessed_weight"]
    assert report.coverage_percentage == expected["coverage_percentage"]
    assert report.competency_results[1].score is None
    assert report.competency_results[1].not_assessed_reason


def test_weighted_score_is_deterministic_and_not_model_supplied() -> None:
    scorecard, turns, draft = _contracts()
    sql_result = draft.competency_results[1].model_copy(
        update={
            "assessment": "scored",
            "score": 2,
            "rating_confidence": "medium",
            "evidence": [
                EvidenceCitation(
                    turn_id="turn-2",
                    quote="I stored an idempotency key with the transaction",
                )
            ],
            "evidence_summary": "Discussed one transactional persistence decision.",
            "not_assessed_reason": None,
        }
    )
    complete = draft.model_copy(
        update={"competency_results": [draft.competency_results[0], sql_result]}
    )

    report = validate_and_score_evaluation(
        complete,
        scorecard,
        turns,
        evaluator_version="test-v1",
    )

    assert report.overall_score == 3.2
    assert report.coverage_percentage == 100


@pytest.mark.parametrize("failure", ["removed", "paraphrased", "assistant", "pending"])
def test_unsupported_evidence_fails_closed(failure: str) -> None:
    scorecard, turns, draft = _contracts()
    if failure == "removed":
        turns = [turn for turn in turns if turn.id != "turn-2"]
    elif failure == "paraphrased":
        citation = (
            draft.competency_results[0]
            .evidence[0]
            .model_copy(
                update={"quote": "The candidate designed a safe retry mechanism."}
            )
        )
        result = draft.competency_results[0].model_copy(update={"evidence": [citation]})
        draft = draft.model_copy(
            update={"competency_results": [result, draft.competency_results[1]]}
        )
    elif failure == "assistant":
        citation = (
            draft.competency_results[0]
            .evidence[0]
            .model_copy(
                update={
                    "turn_id": "turn-1",
                    "quote": "Tell me about an API you designed",
                }
            )
        )
        result = draft.competency_results[0].model_copy(update={"evidence": [citation]})
        draft = draft.model_copy(
            update={"competency_results": [result, draft.competency_results[1]]}
        )
    else:
        turns = [
            turn.model_copy(update={"delivery_status": "pending"})
            if turn.id == "turn-2"
            else turn
            for turn in turns
        ]

    with pytest.raises(EvaluationIntegrityError):
        validate_and_score_evaluation(
            draft,
            scorecard,
            turns,
            evaluator_version="test-v1",
        )


def test_delivery_style_cannot_enter_role_fit_contract() -> None:
    payload = deepcopy(_golden()["valid_draft"])
    payload["competency_results"][0]["delivery_style"] = {
        "confidence": 0.2,
        "accent": "non-native",
    }

    with pytest.raises(ValidationError):
        EvaluationDraft.model_validate(payload)


@pytest.mark.asyncio
async def test_service_regenerates_once_after_invalid_evidence() -> None:
    scorecard, turns, valid = _contracts()
    invalid_payload = valid.model_dump()
    invalid_payload["competency_results"][0]["evidence"][0]["turn_id"] = "missing"
    fake_responses = FakeResponses(
        [EvaluationDraft.model_validate(invalid_payload), valid]
    )

    report = await evaluate_transcript(
        scorecard=scorecard,
        seniority="mid",
        turns=turns,
        interview_section_timings=[{"section": "Technical", "minutes": 10}],
        interview_prompt_version="browser-interview-v1",
        settings=_settings(),
        client=SimpleNamespace(responses=fake_responses),
    )

    assert report.validation_attempts == 2
    assert len(fake_responses.calls) == 2
    second_input = fake_responses.calls[1]["input"]
    assert isinstance(second_input, list)
    repair_payload = json.loads(second_input[1]["content"])
    assert "regeneration_required" in repair_payload


@pytest.mark.asyncio
async def test_service_stops_after_one_regeneration() -> None:
    scorecard, turns, valid = _contracts()
    invalid_payload = valid.model_dump()
    invalid_payload["competency_results"][0]["evidence"][0]["turn_id"] = "missing"
    invalid = EvaluationDraft.model_validate(invalid_payload)
    fake_responses = FakeResponses([invalid, invalid])

    with pytest.raises(EvaluationServiceError) as caught:
        await evaluate_transcript(
            scorecard=scorecard,
            seniority="mid",
            turns=turns,
            interview_section_timings=[],
            interview_prompt_version="browser-interview-v1",
            settings=_settings(),
            client=SimpleNamespace(responses=fake_responses),
        )

    assert len(fake_responses.calls) == 2
    assert caught.value.integrity_issues


@pytest.mark.asyncio
async def test_service_rejects_unordered_transcript_before_model_call() -> None:
    scorecard, turns, valid = _contracts()
    fake_responses = FakeResponses([valid])

    with pytest.raises(EvaluationServiceError) as caught:
        await evaluate_transcript(
            scorecard=scorecard,
            seniority="mid",
            turns=list(reversed(turns)),
            interview_section_timings=[],
            interview_prompt_version="browser-interview-v1",
            settings=_settings(),
            client=SimpleNamespace(responses=fake_responses),
        )

    assert caught.value.status_code == 409
    assert not fake_responses.calls


@pytest.mark.asyncio
async def test_duplicate_evaluation_jobs_yield_instead_of_crashing(
    tmp_path: Path,
) -> None:
    """Two jobs racing for one session must not raise an unhandled IntegrityError.

    ``run_evaluation_job`` guards with ``SELECT ... FOR UPDATE``, which SQLite
    ignores, so duplicate /evaluate requests both observed "no evaluation yet"
    and both inserted. The second violated the unique constraint on
    ``evaluations.session_id`` and crashed the background task.
    """

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from api.models import Base, Evaluation, InterviewSession, User
    from api.services.evaluation_jobs import _commit_unless_claimed

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'race.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC)
    async with factory() as setup:
        setup.add(
            User(
                id="user-1",
                auth_subject="subject-1",
                email="developer@local.test",
                display_name="Local developer",
                created_at=now,
                updated_at=now,
            )
        )
        setup.add(
            InterviewSession(
                id="session-1",
                user_id="user-1",
                title="Race",
                status="TRANSCRIPT_FINALIZING",
                created_at=now,
                updated_at=now,
            )
        )
        await setup.commit()

    def _evaluation() -> Evaluation:
        return Evaluation(
            id=str(uuid.uuid4()),
            session_id="session-1",
            status="EVALUATING",
            schema_version="evidence-report-v1",
            evaluator_version="v1",
            prompt_version="v1",
            model_deployment="gpt-5.6-luna",
            setup_fingerprint="a" * 64,
            transcript_fingerprint="b" * 64,
            transcript_turn_count=2,
            transcript_finalized_at=now,
            scorecard_snapshot={},
            attempt_count=0,
            competency_results=[],
            overall_result=None,
            strengths=[],
            gaps=[],
            practice_exercises=[],
            uncertainty=[],
            created_at=now,
            updated_at=now,
        )

    async with factory() as first, factory() as second:
        first.add(_evaluation())
        second.add(_evaluation())
        assert await _commit_unless_claimed(first, "session-1") is True
        # The loser must report the loss rather than propagate the violation.
        assert await _commit_unless_claimed(second, "session-1") is False

    async with factory() as check:
        rows = (
            (
                await check.execute(
                    select(Evaluation).where(Evaluation.session_id == "session-1")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    await engine.dispose()
