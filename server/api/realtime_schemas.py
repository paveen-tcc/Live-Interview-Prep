"""HTTP contracts for M3 browser Realtime sessions and transcript recovery."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from domain.interview import DeliveryStatus, InputMode, InterviewType, Speaker

ClientTurnId = Annotated[
    str,
    Field(min_length=3, max_length=96, pattern=r"^[A-Za-z0-9_-]+$"),
]


class RealtimeClientSecretRequest(BaseModel):
    input_mode: InputMode
    duration_minutes: int
    interview_type: InterviewType = "technical_behavioral"


class RealtimeClientSecretResponse(BaseModel):
    client_secret: str
    expires_at: int
    calls_url: str
    input_mode: InputMode
    prompt_version: str


class ConnectionStateRequest(BaseModel):
    state: str = Field(pattern="^(connected|reconnecting|failed)$")


class InterviewTurnInput(BaseModel):
    client_turn_id: ClientTurnId
    speaker: Speaker
    # The user-answer limit is runtime configuration and is enforced by the
    # route. Keeping it out of the static schema prevents the API from
    # advertising one limit while Pydantic silently enforces another.
    transcript: str = Field(min_length=1)
    delivery_status: DeliveryStatus = "acknowledged"
    started_at: datetime | None = None
    ended_at: datetime | None = None

    @field_validator("transcript")
    @classmethod
    def visible_transcript(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Transcript must contain visible characters.")
        return value


class InterviewTurnBatchRequest(BaseModel):
    items: list[InterviewTurnInput] = Field(min_length=1, max_length=50)


class InterviewTurnResponse(BaseModel):
    id: str
    client_turn_id: str
    sequence: int
    speaker: Speaker
    transcript: str
    transcription_source: str
    transcription_model: str | None
    transcription_finalized_at: datetime | None
    delivery_status: DeliveryStatus
    started_at: datetime
    ended_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class InterviewRuntimeResponse(BaseModel):
    interview_id: str
    status: str
    input_mode: InputMode
    duration_minutes: int
    started_at: datetime | None
    ends_at: datetime | None
    server_now: datetime
    typed_answer_max_characters: int
    turns: list[InterviewTurnResponse]


class TranscriptionEventRequest(BaseModel):
    kind: Literal[
        "live_transcription_completed",
        "live_transcription_failed",
        "double_transcription_failure",
    ]

    model_config = ConfigDict(extra="forbid")
