"""HTTP schemas for M2 resume, profile, JD, and scorecard workflows."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.intake import (
    JobRequirement,
    ProfileClaim,
    RequirementClass,
    ScorecardCompetency,
    Seniority,
)


class UploadResponse(BaseModel):
    id: str
    original_filename: str
    file_type: str
    media_type: str
    size: int
    sha256: str
    raw_deleted_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ExtractCandidateProfileRequest(BaseModel):
    interview_id: str
    upload_id: str
    replace_existing: bool = False


class CandidateProfileResponse(BaseModel):
    id: str
    source_resume_id: str
    headline: str
    claims: list[ProfileClaim]
    extractor_version: str
    version: int
    created_at: datetime
    updated_at: datetime


class ProfileClaimEdit(BaseModel):
    id: str
    text: str = Field(min_length=1, max_length=2_000)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("Claim text cannot be empty.")
        return normalized


class CandidateProfileUpdateRequest(BaseModel):
    headline: str = Field(min_length=1, max_length=240)
    claims: list[ProfileClaimEdit] = Field(min_length=1, max_length=80)

    @field_validator("headline")
    @classmethod
    def normalize_headline(cls, value: str) -> str:
        return " ".join(value.split()).strip()


class CreateJobTargetRequest(BaseModel):
    interview_id: str
    title: str = Field(min_length=2, max_length=160)
    seniority: Seniority
    raw_description: str = Field(min_length=50, max_length=50_000)

    @field_validator("title", "raw_description")
    @classmethod
    def strip_visible_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("The value must contain visible characters.")
        return normalized


class JobTargetResponse(BaseModel):
    id: str
    title: str
    seniority: Seniority
    raw_description: str
    structured_requirements: list[JobRequirement]
    created_at: datetime
    updated_at: datetime


class GenerateScorecardRequest(BaseModel):
    interview_id: str
    job_target_id: str


class ScorecardResponse(BaseModel):
    id: str
    job_target_id: str
    version: int
    competencies: list[ScorecardCompetency]
    total_weight: int
    created_at: datetime
    updated_at: datetime


class ScorecardCompetencyEdit(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1_000)
    weight: int = Field(ge=1, le=100)
    classification: RequirementClass
    seniority_expectation: str = Field(min_length=1, max_length=1_000)
    evidence_to_collect: list[str] = Field(min_length=1, max_length=8)
    question_families: list[str] = Field(min_length=1, max_length=8)


class UpdateScorecardRequest(BaseModel):
    competencies: list[ScorecardCompetencyEdit] = Field(min_length=2, max_length=10)

    @model_validator(mode="after")
    def validate_weights_and_ids(self) -> UpdateScorecardRequest:
        if sum(item.weight for item in self.competencies) != 100:
            raise ValueError("Scorecard weights must total 100%.")
        identifiers = [item.id for item in self.competencies]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Scorecard competency IDs must be unique.")
        return self


class InterviewSetupResponse(BaseModel):
    upload: UploadResponse | None
    profile: CandidateProfileResponse | None
    job_target: JobTargetResponse | None
    scorecard: ScorecardResponse | None
