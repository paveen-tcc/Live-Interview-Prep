"""Candidate-profile and scorecard domain contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Seniority = Literal["junior", "mid", "senior"]
RequirementClass = Literal["must-have", "trainable", "nice-to-have"]


class SourceReference(BaseModel):
    source_id: str
    label: str
    excerpt: str


class ProfileClaim(BaseModel):
    id: str
    category: Literal["summary", "skill", "experience", "education", "other"]
    text: str = Field(min_length=1, max_length=2_000)
    source: SourceReference
    edited: bool = False
    original_text: str | None = None


class CandidateProfileDocument(BaseModel):
    headline: str = Field(max_length=240)
    claims: list[ProfileClaim] = Field(max_length=80)
    extractor_version: str = "local-rules-v1"


class JobRequirement(BaseModel):
    id: str
    name: str
    classification: RequirementClass
    source: SourceReference


class ScorecardCompetency(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1_000)
    weight: int = Field(ge=1, le=100)
    classification: RequirementClass
    seniority_expectation: str = Field(min_length=1, max_length=1_000)
    evidence_to_collect: list[str] = Field(min_length=1, max_length=8)
    question_families: list[str] = Field(min_length=1, max_length=8)
    source_references: list[SourceReference] = Field(min_length=1, max_length=8)


class ScorecardDocument(BaseModel):
    competencies: list[ScorecardCompetency] = Field(min_length=2, max_length=10)
    generator_version: str = "backend-template-v1"

    @model_validator(mode="after")
    def weights_total_one_hundred(self) -> ScorecardDocument:
        if sum(item.weight for item in self.competencies) != 100:
            raise ValueError("Scorecard weights must total 100%.")
        identifiers = [item.id for item in self.competencies]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Scorecard competency IDs must be unique.")
        return self
