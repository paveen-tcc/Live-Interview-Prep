"""Public M4 evidence-backed evaluation and report contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from domain.intake import RequirementClass

from .delivery_schemas import DeliveryCoachingResponse


class ReportEvidence(BaseModel):
    turn_id: str
    sequence: int
    quote: str = Field(min_length=1, max_length=1_000)


class CompetencyReportResult(BaseModel):
    competency_id: str
    name: str
    weight: int = Field(ge=1, le=100)
    classification: RequirementClass
    assessment: Literal["scored", "not_assessed"]
    score: int | None = Field(default=None, ge=1, le=5)
    rating_confidence: Literal["low", "medium", "high"] | None = None
    evidence: list[ReportEvidence]
    evidence_summary: str | None
    gaps: list[str]
    recommendations: list[str]
    not_assessed_reason: str | None = None


class PracticeExercise(BaseModel):
    title: str
    competency_ids: list[str] = Field(min_length=1)
    instruction: str
    success_criteria: list[str] = Field(min_length=1, max_length=6)


class EvaluationStatusResponse(BaseModel):
    interview_id: str
    status: str


class InterviewReportResponse(BaseModel):
    interview_id: str
    status: Literal["REPORT_READY"]
    evaluator_version: str
    prompt_version: str
    overall_score: float | None = Field(default=None, ge=1, le=5)
    assessed_weight: int = Field(ge=0, le=100)
    total_weight: int = Field(ge=1, le=100)
    coverage_percentage: float = Field(ge=0, le=100)
    competency_results: list[CompetencyReportResult]
    strengths: list[str]
    gaps: list[str]
    practice_exercises: list[PracticeExercise]
    uncertainty: list[str]
    delivery_coaching: DeliveryCoachingResponse
    completed_at: datetime
