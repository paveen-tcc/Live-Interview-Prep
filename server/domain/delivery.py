"""Deterministic, consent-gated speaking-delivery observations.

This module deliberately operates on observable transcript and timing data only.
It does not infer internal state and its results never participate in role-fit scores.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, Field

BASELINE_TURN_COUNT = 2
LONG_PAUSE_MS = 700

ObservationCategory = Literal[
    "pace",
    "pauses",
    "fillers",
    "response_delay",
    "interruptions",
    "answer_length",
]


class DeliveryTurnMetric(BaseModel):
    turn_id: str
    sequence: int = Field(ge=1)
    word_count: int = Field(ge=0)
    speaking_duration_ms: int = Field(gt=0)
    answer_duration_ms: int = Field(gt=0)
    words_per_minute: float = Field(ge=0)
    pause_count: int = Field(ge=0)
    total_pause_ms: int = Field(ge=0)
    longest_pause_ms: int = Field(ge=0)
    filler_count: int = Field(ge=0)
    fillers_per_100_words: float = Field(ge=0)
    response_delay_ms: int | None = Field(default=None, ge=0)
    interruption_count: int | None = Field(default=None, ge=0)


class DeliveryBaseline(BaseModel):
    turn_count: int = Field(ge=BASELINE_TURN_COUNT)
    turn_ids: list[str] = Field(min_length=BASELINE_TURN_COUNT)
    words_per_minute: float = Field(ge=0)
    filler_words_per_100_words: float = Field(ge=0)
    average_pause_ms: float | None = Field(default=None, ge=0)
    average_response_delay_ms: float | None = Field(default=None, ge=0)


class DeliveryObservation(BaseModel):
    turn_id: str
    category: ObservationCategory
    text: str = Field(min_length=1, max_length=400)


_WORD_PATTERN = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)
_FILLER_PATTERN = re.compile(r"\b(?:um+|uh+|erm+|er|you\s+know)\b", re.IGNORECASE)


def _rounded(value: Decimal, places: str = "0.1") -> float:
    return float(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))


def count_words(transcript: str) -> int:
    return len(_WORD_PATTERN.findall(transcript))


def count_fillers(transcript: str) -> int:
    return len(_FILLER_PATTERN.findall(transcript))


def calculate_turn_metric(
    *,
    turn_id: str,
    sequence: int,
    transcript: str,
    speech_segments_ms: list[tuple[int, int]],
    response_delay_ms: int | None,
    interruption_count: int | None,
) -> DeliveryTurnMetric:
    """Calculate one answer's metrics from ordered speech intervals."""

    if not speech_segments_ms:
        raise ValueError("At least one speech segment is required.")
    previous_end = -1
    for started_ms, ended_ms in speech_segments_ms:
        if started_ms < 0 or ended_ms <= started_ms:
            raise ValueError("Speech segments must have positive durations.")
        if started_ms < previous_end:
            raise ValueError("Speech segments cannot overlap.")
        previous_end = ended_ms

    word_count = count_words(transcript)
    filler_count = count_fillers(transcript)
    speaking_duration_ms = sum(end - start for start, end in speech_segments_ms)
    answer_duration_ms = speech_segments_ms[-1][1] - speech_segments_ms[0][0]
    pauses = [
        speech_segments_ms[index][0] - speech_segments_ms[index - 1][1]
        for index in range(1, len(speech_segments_ms))
    ]
    long_pauses = [pause for pause in pauses if pause >= LONG_PAUSE_MS]
    wpm = (
        Decimal(word_count) * Decimal(60_000) / Decimal(speaking_duration_ms)
        if word_count
        else Decimal(0)
    )
    fillers_per_100 = (
        Decimal(filler_count) * Decimal(100) / Decimal(word_count)
        if word_count
        else Decimal(0)
    )
    return DeliveryTurnMetric(
        turn_id=turn_id,
        sequence=sequence,
        word_count=word_count,
        speaking_duration_ms=speaking_duration_ms,
        answer_duration_ms=answer_duration_ms,
        words_per_minute=_rounded(wpm),
        pause_count=len(long_pauses),
        total_pause_ms=sum(long_pauses),
        longest_pause_ms=max(long_pauses, default=0),
        filler_count=filler_count,
        fillers_per_100_words=_rounded(fillers_per_100),
        response_delay_ms=response_delay_ms,
        interruption_count=interruption_count,
    )


def establish_baseline(
    metrics: list[DeliveryTurnMetric],
) -> DeliveryBaseline | None:
    """Use the first two observed answers as the candidate's own baseline period."""

    ordered = sorted(metrics, key=lambda metric: metric.sequence)
    if len(ordered) < BASELINE_TURN_COUNT:
        return None
    baseline_metrics = ordered[:BASELINE_TURN_COUNT]
    total_words = sum(metric.word_count for metric in baseline_metrics)
    total_speaking_ms = sum(metric.speaking_duration_ms for metric in baseline_metrics)
    total_fillers = sum(metric.filler_count for metric in baseline_metrics)
    pause_durations = [
        metric.total_pause_ms / metric.pause_count
        for metric in baseline_metrics
        if metric.pause_count
    ]
    response_delays = [
        metric.response_delay_ms
        for metric in baseline_metrics
        if metric.response_delay_ms is not None
    ]
    return DeliveryBaseline(
        turn_count=len(baseline_metrics),
        turn_ids=[metric.turn_id for metric in baseline_metrics],
        words_per_minute=_rounded(
            Decimal(total_words) * Decimal(60_000) / Decimal(total_speaking_ms)
        ),
        filler_words_per_100_words=(
            _rounded(Decimal(total_fillers) * Decimal(100) / Decimal(total_words))
            if total_words
            else 0
        ),
        average_pause_ms=(
            _rounded(Decimal(str(sum(pause_durations) / len(pause_durations))))
            if pause_durations
            else None
        ),
        average_response_delay_ms=(
            _rounded(Decimal(sum(response_delays)) / Decimal(len(response_delays)))
            if response_delays
            else None
        ),
    )


def build_observations(
    metrics: list[DeliveryTurnMetric], baseline: DeliveryBaseline | None
) -> list[DeliveryObservation]:
    observations: list[DeliveryObservation] = []
    baseline_ids = set(baseline.turn_ids) if baseline else set()
    for metric in sorted(metrics, key=lambda item: item.sequence):
        if baseline is None or metric.turn_id in baseline_ids:
            pace_text = (
                f"Speaking pace was {metric.words_per_minute:g} words per minute "
                "during the individual baseline period."
            )
        else:
            change = metric.words_per_minute - baseline.words_per_minute
            direction = "above" if change >= 0 else "below"
            pace_text = (
                f"Speaking pace was {metric.words_per_minute:g} words per minute, "
                f"{abs(change):g} {direction} your baseline."
            )
        observations.append(
            DeliveryObservation(turn_id=metric.turn_id, category="pace", text=pace_text)
        )
        observations.append(
            DeliveryObservation(
                turn_id=metric.turn_id,
                category="pauses",
                text=(
                    f"This answer contained {metric.pause_count} pause(s) lasting "
                    f"at least {LONG_PAUSE_MS / 1000:g} seconds; the longest was "
                    f"{metric.longest_pause_ms / 1000:g} seconds."
                ),
            )
        )
        observations.append(
            DeliveryObservation(
                turn_id=metric.turn_id,
                category="fillers",
                text=(
                    f"This answer contained {metric.filler_count} filler phrase(s) "
                    f"across {metric.word_count} words."
                ),
            )
        )
        if metric.response_delay_ms is not None:
            observations.append(
                DeliveryObservation(
                    turn_id=metric.turn_id,
                    category="response_delay",
                    text=(
                        "The answer began "
                        f"{metric.response_delay_ms / 1000:g} seconds after the "
                        "interviewer finished."
                    ),
                )
            )
        if metric.interruption_count is not None:
            observations.append(
                DeliveryObservation(
                    turn_id=metric.turn_id,
                    category="interruptions",
                    text=(
                        "The answer overlapped the interviewer "
                        f"{metric.interruption_count} time(s)."
                    ),
                )
            )
        observations.append(
            DeliveryObservation(
                turn_id=metric.turn_id,
                category="answer_length",
                text=(
                    f"The answer used {metric.word_count} words over "
                    f"{metric.answer_duration_ms / 1000:g} seconds."
                ),
            )
        )
    return observations


def build_suggestions(
    metrics: list[DeliveryTurnMetric], baseline: DeliveryBaseline | None
) -> list[str]:
    if not metrics:
        return []
    suggestions: list[str] = []
    later_metrics = (
        [metric for metric in metrics if metric.turn_id not in set(baseline.turn_ids)]
        if baseline
        else []
    )
    if baseline and any(
        abs(metric.words_per_minute - baseline.words_per_minute)
        >= max(20, baseline.words_per_minute * 0.2)
        for metric in later_metrics
    ):
        suggestions.append(
            "When your pace changes most from your baseline, pause between the "
            "problem, options, decision, and trade-off."
        )
    if any(metric.pause_count for metric in metrics):
        suggestions.append(
            "Before a complex answer, take one deliberate pause and outline two or "
            "three points."
        )
    if any(metric.filler_count for metric in metrics):
        suggestions.append(
            "Replace filler phrases with a brief silent pause while choosing the next "
            "point."
        )
    if any((metric.interruption_count or 0) > 0 for metric in metrics):
        suggestions.append(
            "Wait for the interviewer to finish, then begin after a short pause."
        )
    if any(metric.answer_duration_ms >= 120_000 for metric in metrics):
        suggestions.append(
            "For longer answers, signpost the situation, action, result, and lesson."
        )
    return suggestions
