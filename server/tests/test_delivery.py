from __future__ import annotations

from domain.delivery import (
    build_observations,
    build_suggestions,
    calculate_turn_metric,
    establish_baseline,
)


def test_delivery_metrics_use_observable_timing_and_personal_baseline() -> None:
    first = calculate_turn_metric(
        turn_id="turn-1",
        sequence=1,
        transcript="Um I designed the API and explained the database trade-off.",
        speech_segments_ms=[(0, 2_000), (3_000, 5_000)],
        response_delay_ms=800,
        interruption_count=0,
    )
    second = calculate_turn_metric(
        turn_id="turn-2",
        sequence=2,
        transcript="I compared two options and chose the safer retry design.",
        speech_segments_ms=[(0, 4_000)],
        response_delay_ms=1_000,
        interruption_count=0,
    )
    later = calculate_turn_metric(
        turn_id="turn-3",
        sequence=3,
        transcript=(
            "Uh I would first isolate the failure and then add a regression test."
        ),
        speech_segments_ms=[(0, 1_500), (2_500, 4_000)],
        response_delay_ms=1_200,
        interruption_count=1,
    )

    baseline = establish_baseline([first, second, later])
    assert baseline is not None
    assert baseline.turn_ids == ["turn-1", "turn-2"]
    assert first.pause_count == 1
    assert first.filler_count == 1
    assert later.interruption_count == 1

    observations = build_observations([first, second, later], baseline)
    suggestions = build_suggestions([first, second, later], baseline)
    rendered = (
        " ".join(item.text for item in observations) + " " + " ".join(suggestions)
    )
    for forbidden in ("stress", "emotion", "deception", "personality", "confidence"):
        assert forbidden not in rendered.lower()


def test_delivery_baseline_requires_two_answers() -> None:
    metric = calculate_turn_metric(
        turn_id="turn-1",
        sequence=1,
        transcript="A concise answer.",
        speech_segments_ms=[(0, 1_000)],
        response_delay_ms=None,
        interruption_count=None,
    )

    assert establish_baseline([metric]) is None
