"""Public API schemas shared by the workspace foundation."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str

    model_config = ConfigDict(from_attributes=True)


class CreateInterviewRequest(BaseModel):
    title: str = Field(
        default="Untitled practice session",
        min_length=1,
        max_length=120,
    )

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Title must contain visible characters.")
        return normalized


class InterviewResponse(BaseModel):
    id: str
    title: str
    status: str
    profile_id: str | None
    scorecard_id: str | None
    duration_minutes: int
    interview_type: str
    input_mode: str
    started_at: datetime | None
    ended_at: datetime | None
    prompt_version: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InterviewListResponse(BaseModel):
    items: list[InterviewResponse]


class CapabilityResponse(BaseModel):
    text_dev_mode_enabled: bool
    realtime_configured: bool
    live_transcription_configured: bool
    final_transcription_configured: bool
    typed_answer_max_characters: int
    supported_durations: list[int]
