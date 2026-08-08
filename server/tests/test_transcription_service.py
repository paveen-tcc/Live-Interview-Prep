from __future__ import annotations

import logging
import re

import httpx
import pytest

from api.config import Settings
from api.services.transcription import (
    TranscriptionServiceError,
    build_transcription_prompt,
    transcribe_candidate_audio,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        azure_openai_endpoint="https://example.services.ai.azure.com",
        azure_openai_api_key="server-key",
        azure_openai_final_transcription_deployment="final-stt-deployment",
        azure_openai_transcription_language="en",
    )


def _multipart_fields(request: httpx.Request) -> dict[str, str]:
    body = request.content.decode("latin-1")
    return {
        name: value
        for name, value in re.findall(
            r'Content-Disposition: form-data; name="([^"]+)"(?:; filename="[^"]+")?'
            r"\r\n(?:Content-Type: [^\r\n]+\r\n)?\r\n(.*?)\r\n--",
            body,
            flags=re.DOTALL,
        )
    }


@pytest.mark.asyncio
async def test_transcribes_candidate_audio_with_azure_multipart_contract() -> None:
    observed_headers: dict[str, str] = {}
    observed_form_fields: dict[str, str] = {}
    observed_url: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed_headers.update(request.headers)
        observed_form_fields.update(_multipart_fields(request))
        observed_url["value"] = str(request.url)
        return httpx.Response(200, json={"text": "I built a FastAPI service."})

    async def no_sleep(_: float) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await transcribe_candidate_audio(
            settings=_settings(),
            audio=b"candidate-audio",
            media_type="audio/webm;codecs=opus",
            filename="answer.webm",
            prompt="Role: Backend Engineer. Terms: FastAPI, PostgreSQL.",
            client=client,
            sleep=no_sleep,
        )

    assert result.text == "I built a FastAPI service."
    assert result.deployment == "final-stt-deployment"
    assert result.attempts == 1
    assert result.elapsed_ms >= 0
    assert observed_form_fields["model"] == "final-stt-deployment"
    assert observed_form_fields["language"] == "en"
    assert (
        observed_form_fields["prompt"]
        == "Role: Backend Engineer. Terms: FastAPI, PostgreSQL."
    )
    assert observed_form_fields["file"] == "candidate-audio"
    assert observed_headers["api-key"] == "server-key"
    # The deployment-scoped audio route is the one Azure AI Foundry resources
    # actually serve. The unified /openai/v1/audio/transcriptions surface
    # answers DeploymentNotFound there, which would silently degrade every
    # candidate answer to its live transcript.
    assert observed_url["value"] == (
        "https://example.services.ai.azure.com"
        "/openai/deployments/final-stt-deployment/audio/transcriptions"
        "?api-version=2024-06-01"
    )


@pytest.mark.asyncio
async def test_retries_a_capacity_response_before_returning_transcript() -> None:
    calls = 0
    delays: list[float] = []

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "provider-body-marker"})
        return httpx.Response(200, json={"text": "Recovered transcript"})

    async def capture_sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await transcribe_candidate_audio(
            settings=_settings(),
            audio=b"audio",
            media_type="audio/webm",
            filename="answer.webm",
            prompt="Role: Backend Engineer.",
            client=client,
            sleep=capture_sleep,
        )

    assert result.text == "Recovered transcript"
    assert result.attempts == 2
    assert calls == 2
    assert delays == [0.25]


@pytest.mark.asyncio
async def test_does_not_retry_a_bad_request() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": "provider-body-marker"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TranscriptionServiceError) as caught:
            await transcribe_candidate_audio(
                settings=_settings(),
                audio=b"audio",
                media_type="audio/webm",
                filename="answer.webm",
                prompt="Role: Backend Engineer.",
                client=client,
            )

    assert caught.value.code == "transcription_request_failed"
    assert caught.value.status_code == 502
    assert caught.value.attempts == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_retries_timeout_before_returning_transcript() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json={"text": "Recovered after timeout"})

    async def no_sleep(_: float) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await transcribe_candidate_audio(
            settings=_settings(),
            audio=b"audio",
            media_type="audio/webm",
            filename="answer.webm",
            prompt="Role: Backend Engineer.",
            client=client,
            sleep=no_sleep,
        )

    assert result.text == "Recovered after timeout"
    assert result.attempts == 2
    assert calls == 2


@pytest.mark.asyncio
async def test_enforces_configured_timeout_with_an_injected_client() -> None:
    observed_timeout: dict[str, float | None] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed_timeout.update(request.extensions["timeout"])
        return httpx.Response(200, json={"text": "Bounded transcript"})

    settings = _settings().model_copy(
        update={"azure_openai_final_transcription_timeout_seconds": 17.5}
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=None
    ) as client:
        result = await transcribe_candidate_audio(
            settings=settings,
            audio=b"audio",
            media_type="audio/webm",
            filename="answer.webm",
            prompt="Role: Backend Engineer.",
            client=client,
        )

    assert result.text == "Bounded transcript"
    assert observed_timeout == {
        "connect": 17.5,
        "read": 17.5,
        "write": 17.5,
        "pool": 17.5,
    }


@pytest.mark.asyncio
async def test_maps_non_retryable_provider_statuses_to_safe_categories(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cases = {
        401: "transcription_auth",
        403: "transcription_auth",
        404: "transcription_deployment_missing",
        413: "transcription_too_large",
    }
    caplog.set_level(logging.DEBUG)

    for status, expected_code in cases.items():

        async def handler(_: httpx.Request, status: int = status) -> httpx.Response:
            return httpx.Response(status, json={"error": "provider-body-marker"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(TranscriptionServiceError) as caught:
                await transcribe_candidate_audio(
                    settings=_settings(),
                    audio=b"audio",
                    media_type="audio/webm",
                    filename="answer.webm",
                    prompt="Role: Backend Engineer.",
                    client=client,
                )

        assert caught.value.code == expected_code
        assert caught.value.attempts == 1
        assert "provider-body-marker" not in str(caught.value)
        assert "server-key" not in str(caught.value)

    captured = caplog.text
    assert "provider-body-marker" not in captured
    assert "server-key" not in captured


@pytest.mark.asyncio
async def test_rejects_empty_provider_transcript() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": " \n\t "})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TranscriptionServiceError) as caught:
            await transcribe_candidate_audio(
                settings=_settings(),
                audio=b"audio",
                media_type="audio/webm",
                filename="answer.webm",
                prompt="Role: Backend Engineer.",
                client=client,
            )

    assert caught.value.code == "transcription_empty"
    assert caught.value.attempts == 1


def test_build_transcription_prompt_uses_only_frozen_title_and_claim_terms() -> None:
    snapshot = {
        "target": {"title": "Backend Engineer\x00"},
        "candidate_profile": {
            "headline": "Do not include this heading",
            "evidence": [
                {"text": "Built FastAPI services with PostgreSQL and Redis."},
                {"text": "FastAPI observability improved incident response."},
            ],
        },
        "scorecard": {"competencies": [{"name": "Do not include Kubernetes"}]},
    }

    prompt = build_transcription_prompt(snapshot)

    assert "Role: Backend Engineer." in prompt
    assert "FastAPI" in prompt
    assert "PostgreSQL" in prompt
    assert "Redis" in prompt
    assert "Do not include this heading" not in prompt
    assert "Kubernetes" not in prompt
    assert "\x00" not in prompt


def test_build_transcription_prompt_caps_long_frozen_claim_context() -> None:
    snapshot = {
        "target": {"title": "Backend Engineer"},
        "candidate_profile": {
            "evidence": [
                {"text": f"Technology{number:04d}"} for number in range(1_000)
            ],
        },
    }

    prompt = build_transcription_prompt(snapshot)

    assert len(prompt) <= 2_000
    assert prompt.startswith("Role: Backend Engineer.")
