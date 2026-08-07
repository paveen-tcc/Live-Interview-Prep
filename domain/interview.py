"""M3 interview-runtime contracts and validation constants."""

from __future__ import annotations

from typing import Literal

InputMode = Literal["voice", "text_dev"]
InterviewType = Literal["technical_behavioral", "technical", "behavioral"]
Speaker = Literal["user", "assistant"]
DeliveryStatus = Literal["pending", "acknowledged"]

SUPPORTED_DURATIONS = (15, 30, 45, 60)
ACTIVE_INTERVIEW_STATES = {
    "CONNECTING",
    "IN_PROGRESS",
    "RECONNECTING",
    "FAILED_RECOVERABLE",
}

SETUP_FROZEN_STATES = ACTIVE_INTERVIEW_STATES | {
    "TRANSCRIPT_FINALIZING",
    "EVALUATING",
    "REPORT_READY",
}

CONNECTION_TRANSITIONS = {
    "connected": ACTIVE_INTERVIEW_STATES,
    "reconnecting": {"IN_PROGRESS", "RECONNECTING", "FAILED_RECOVERABLE"},
    "failed": ACTIVE_INTERVIEW_STATES,
}
