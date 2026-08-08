"""Public M5 consent and observable speaking-delivery contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from domain.delivery import DeliveryBaseline, DeliveryObservation, DeliveryTurnMetric


class DeliveryConsentRequest(BaseModel):
    enabled: bool
    consent_version: Literal["delivery-v1"]


class SpeechSegment(BaseModel):
    started_at: datetime
    ended_at: datetime

    @model_validator(mode="after")
    def positive_duration(self) -> SpeechSegment:
        if self.ended_at <= self.started_at:
            raise ValueError("Speech segment end must follow its start.")
        return self


class DeliveryTurnObservation(BaseModel):
    turn_id: str = Field(min_length=1, max_length=96)
    speech_segments: list[SpeechSegment] = Field(min_length=1, max_length=500)


class DeliveryObservationBatchRequest(BaseModel):
    items: list[DeliveryTurnObservation] = Field(min_length=1, max_length=40)


class DeliveryCoachingResponse(BaseModel):
    interview_id: str
    status: Literal["available", "collecting", "unavailable", "disabled", "deleted"]
    consented: bool
    consent_version: str | None
    unavailable_reason: (
        Literal["consent_required", "text_input_mode", "no_observations"] | None
    )
    baseline: DeliveryBaseline | None
    metrics: list[DeliveryTurnMetric]
    observations: list[DeliveryObservation]
    suggestions: list[str]
