"""Private-alpha privacy, quota, and usage contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DeleteSessionRequest(BaseModel):
    confirmation: Literal["DELETE"]


class DeleteAccountRequest(BaseModel):
    confirmation: Literal["DELETE MY ACCOUNT"]


class UsageSummaryResponse(BaseModel):
    period_started_at: datetime
    daily_interview_quota: int
    daily_interviews_used: int
    events: dict[str, int]
    estimated_cost_usd: str
    cost_status: Literal["estimated", "unavailable"]
    transcript_retention_days: int
    delivery_metrics_retention_days: int
