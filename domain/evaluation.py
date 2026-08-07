"""Evidence-backed evaluation contracts and deterministic report scoring."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.intake import ScorecardDocument

RatingConfidence = Literal["low", "medium", "high"]
TranscriptSpeaker = Literal["user", "assistant"]
EvaluationDeliveryStatus = Literal["pending", "acknowledged"]
Assessment = Literal["scored", "not_assessed"]


class StrictContract(BaseModel):
    """Reject fields outside the versioned evaluator contract."""

    model_config = ConfigDict(extra="forbid")


class EvaluationTranscriptTurn(StrictContract):
    id: str = Field(min_length=1, max_length=96)
    sequence: int = Field(ge=1)
    speaker: TranscriptSpeaker
    transcript: str = Field(min_length=1, max_length=50_000)
    delivery_status: EvaluationDeliveryStatus = "acknowledged"


class EvidenceCitation(StrictContract):
    turn_id: str = Field(min_length=1, max_length=96)
    quote: str = Field(min_length=3, max_length=800)

    @field_validator("quote")
    @classmethod
    def strip_quote_edges(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 3:
            raise ValueError("Evidence quotes must contain at least three characters.")
        return stripped


class CompetencyEvaluation(StrictContract):
    competency_id: str = Field(min_length=1, max_length=160)
    assessment: Assessment
    score: int | None = Field(default=None, ge=1, le=5)
    rating_confidence: RatingConfidence | None = None
    evidence: list[EvidenceCitation] = Field(default_factory=list, max_length=8)
    evidence_summary: str | None = Field(default=None, max_length=1_500)
    gaps: list[str] = Field(default_factory=list, max_length=8)
    recommendations: list[str] = Field(default_factory=list, max_length=8)
    not_assessed_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def assessed_result_has_evidence(self) -> CompetencyEvaluation:
        if self.assessment == "not_assessed":
            if self.score is not None:
                raise ValueError("A not-assessed competency cannot have a score.")
            if not self.not_assessed_reason or not self.not_assessed_reason.strip():
                raise ValueError(
                    "A not-assessed competency requires not_assessed_reason."
                )
            if self.rating_confidence is not None:
                raise ValueError(
                    "A not-assessed competency cannot have rating_confidence."
                )
            if self.evidence:
                raise ValueError("A not-assessed competency cannot cite evidence.")
            return self

        if self.score is None:
            raise ValueError("A scored competency requires a score.")
        if self.rating_confidence is None:
            raise ValueError("A scored competency requires rating_confidence.")
        if not self.evidence:
            raise ValueError("A scored competency requires transcript evidence.")
        if not self.evidence_summary or not self.evidence_summary.strip():
            raise ValueError("A scored competency requires an evidence summary.")
        if self.not_assessed_reason is not None:
            raise ValueError("A scored competency cannot include not_assessed_reason.")
        return self

    @property
    def evidence_turn_ids(self) -> list[str]:
        return [citation.turn_id for citation in self.evidence]


class PracticeExercise(StrictContract):
    title: str = Field(min_length=1, max_length=160)
    competency_ids: list[str] = Field(min_length=1, max_length=5)
    instruction: str = Field(min_length=1, max_length=1_500)
    success_criteria: list[str] = Field(min_length=1, max_length=6)


class EvaluationDraft(StrictContract):
    """Schema-constrained model output before evidence integrity validation."""

    competency_results: list[CompetencyEvaluation] = Field(min_length=1, max_length=10)
    strength_competency_ids: list[str] = Field(default_factory=list, max_length=5)
    gap_competency_ids: list[str] = Field(default_factory=list, max_length=5)
    practice_exercises: list[PracticeExercise] = Field(
        default_factory=list, max_length=8
    )


class EvaluationReport(StrictContract):
    """Validated results plus server-computed overall score and coverage."""

    evaluator_version: str = Field(min_length=1, max_length=120)
    competency_results: list[CompetencyEvaluation]
    overall_score: float | None = Field(default=None, ge=1, le=5)
    assessed_weight: int = Field(ge=0, le=100)
    total_weight: int = Field(ge=1, le=100)
    coverage_percentage: int = Field(ge=0, le=100)
    strength_competency_ids: list[str]
    gap_competency_ids: list[str]
    practice_exercises: list[PracticeExercise]
    evidence_locations: list[EvidenceQuoteLocation]
    validation_attempts: int = Field(ge=1, le=2)


class EvidenceQuoteLocation(StrictContract):
    competency_id: str
    turn_id: str
    quote: str
    quote_start: int = Field(ge=0)
    quote_end: int = Field(ge=1)


class EvaluationIntegrityError(ValueError):
    """The model output cannot be supported by the finalized transcript."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__("; ".join(issues))


def validate_and_score_evaluation(
    draft: EvaluationDraft,
    scorecard: ScorecardDocument,
    turns: list[EvaluationTranscriptTurn],
    *,
    evaluator_version: str,
    validation_attempts: int = 1,
) -> EvaluationReport:
    """Validate cited candidate evidence and compute the weighted role-fit score."""

    issues = _integrity_issues(draft, scorecard, turns)
    if issues:
        raise EvaluationIntegrityError(issues)

    weights = {item.id: item.weight for item in scorecard.competencies}
    assessed = [item for item in draft.competency_results if item.score is not None]
    assessed_weight = sum(weights[item.competency_id] for item in assessed)
    weighted_points = sum(
        Decimal(weights[item.competency_id]) * Decimal(item.score)
        for item in assessed
        if item.score is not None
    )
    overall_score = None
    if assessed_weight:
        overall_score = float(
            (weighted_points / Decimal(assessed_weight)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        )

    turn_by_id = {turn.id: turn for turn in turns}
    evidence_locations = [
        EvidenceQuoteLocation(
            competency_id=result.competency_id,
            turn_id=citation.turn_id,
            quote=citation.quote,
            quote_start=turn_by_id[citation.turn_id].transcript.index(citation.quote),
            quote_end=(
                turn_by_id[citation.turn_id].transcript.index(citation.quote)
                + len(citation.quote)
            ),
        )
        for result in draft.competency_results
        for citation in result.evidence
    ]

    return EvaluationReport(
        evaluator_version=evaluator_version,
        competency_results=draft.competency_results,
        overall_score=overall_score,
        assessed_weight=assessed_weight,
        total_weight=sum(weights.values()),
        coverage_percentage=assessed_weight,
        strength_competency_ids=draft.strength_competency_ids,
        gap_competency_ids=draft.gap_competency_ids,
        practice_exercises=draft.practice_exercises,
        evidence_locations=evidence_locations,
        validation_attempts=validation_attempts,
    )


def _integrity_issues(
    draft: EvaluationDraft,
    scorecard: ScorecardDocument,
    turns: list[EvaluationTranscriptTurn],
) -> list[str]:
    issues: list[str] = []
    expected_ids = [item.id for item in scorecard.competencies]
    result_ids = [item.competency_id for item in draft.competency_results]
    if len(result_ids) != len(set(result_ids)):
        issues.append("competency_results contains duplicate competency IDs")
    missing = sorted(set(expected_ids) - set(result_ids))
    unknown = sorted(set(result_ids) - set(expected_ids))
    if missing:
        issues.append(f"missing competency results: {', '.join(missing)}")
    if unknown:
        issues.append(f"unknown competency results: {', '.join(unknown)}")

    sequence_values = [turn.sequence for turn in turns]
    turn_ids = [turn.id for turn in turns]
    if len(sequence_values) != len(set(sequence_values)):
        issues.append("transcript contains duplicate sequence numbers")
    if len(turn_ids) != len(set(turn_ids)):
        issues.append("transcript contains duplicate turn IDs")
    if sequence_values != sorted(sequence_values):
        issues.append("transcript turns are not in sequence order")

    turn_by_id = {turn.id: turn for turn in turns}
    for result in draft.competency_results:
        cited_ids: set[str] = set()
        for citation in result.evidence:
            if citation.turn_id in cited_ids:
                issues.append(
                    f"{result.competency_id} cites turn {citation.turn_id} "
                    "more than once"
                )
                continue
            cited_ids.add(citation.turn_id)
            turn = turn_by_id.get(citation.turn_id)
            if turn is None:
                issues.append(
                    f"{result.competency_id} cites nonexistent turn {citation.turn_id}"
                )
                continue
            if turn.speaker != "user":
                issues.append(
                    f"{result.competency_id} cites non-candidate turn "
                    f"{citation.turn_id}"
                )
                continue
            if turn.delivery_status != "acknowledged":
                issues.append(
                    f"{result.competency_id} cites unacknowledged turn "
                    f"{citation.turn_id}"
                )
                continue
            if citation.quote not in turn.transcript:
                issues.append(
                    f"{result.competency_id} quote does not match turn "
                    f"{citation.turn_id}"
                )

    known = set(expected_ids)
    for field_name, identifiers in (
        ("strength_competency_ids", draft.strength_competency_ids),
        ("gap_competency_ids", draft.gap_competency_ids),
    ):
        invalid = sorted(set(identifiers) - known)
        if invalid:
            issues.append(f"{field_name} contains unknown IDs: {', '.join(invalid)}")
        if len(identifiers) != len(set(identifiers)):
            issues.append(f"{field_name} contains duplicate IDs")

    for exercise in draft.practice_exercises:
        invalid = sorted(set(exercise.competency_ids) - known)
        if invalid:
            issues.append(
                f"practice exercise {exercise.title!r} contains unknown IDs: "
                f"{', '.join(invalid)}"
            )
    return issues
