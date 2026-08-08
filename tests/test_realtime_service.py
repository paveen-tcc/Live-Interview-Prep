from __future__ import annotations

import json

import httpx
import pytest

from api.config import Settings
from api.services.realtime import (
    RealtimeServiceError,
    create_realtime_client_secret,
)


def _settings() -> Settings:
    return Settings(
        app_env="test",
        azure_openai_endpoint="https://example.services.ai.azure.com",
        azure_openai_api_key="permanent-server-key",
        azure_openai_realtime_deployment="realtime-deployment",
        azure_openai_realtime_voice="marin",
        azure_openai_realtime_transcription_model="live-stt-deployment",
    )


def test_dual_transcription_configuration_uses_explicit_deployment_names() -> None:
    settings = Settings(
        _env_file=None,
        azure_openai_endpoint="https://example.services.ai.azure.com",
        azure_openai_api_key="server-key",
        azure_openai_realtime_deployment="interviewer-deployment",
        azure_openai_realtime_transcription_model="live-stt-deployment",
        azure_openai_final_transcription_deployment="final-stt-deployment",
        azure_openai_transcription_language="en",
        azure_openai_transcription_delay="low",
    )

    assert settings.live_transcription_configured is True
    assert settings.final_transcription_configured is True


def test_live_transcription_is_not_configured_without_a_deployment_name() -> None:
    settings = Settings(
        _env_file=None,
        azure_openai_endpoint="https://example.services.ai.azure.com",
        azure_openai_api_key="server-key",
        azure_openai_realtime_deployment="interviewer-deployment",
    )

    assert settings.live_transcription_configured is False


@pytest.mark.asyncio
async def test_client_secret_uses_ga_contract_without_exposing_server_key() -> None:
    observed: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["api_key"] = request.headers["api-key"]
        observed["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"value": "ek_temporary", "expires_at": 1_786_000_000},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        secret = await create_realtime_client_secret(
            settings=_settings(),
            instructions="server-owned prompt",
            input_mode="text_dev",
            client=client,
        )

    assert observed["url"] == (
        "https://example.services.ai.azure.com/openai/v1/realtime/client_secrets"
    )
    assert observed["api_key"] == "permanent-server-key"
    body = observed["body"]
    assert isinstance(body, dict)
    assert body["session"]["type"] == "realtime"
    assert body["session"]["output_modalities"] == ["audio"]
    assert "input" not in body["session"]["audio"]
    assert secret.value == "ek_temporary"
    assert "permanent-server-key" not in repr(secret)
    assert secret.calls_url.endswith("/openai/v1/realtime/calls?webrtcfilter=on")


@pytest.mark.asyncio
async def test_voice_secret_configures_vad_and_input_transcription() -> None:
    observed: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.content))
        return httpx.Response(200, json={"value": "ek_voice", "expires_at": 123})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await create_realtime_client_secret(
            settings=_settings(),
            instructions="prompt",
            input_mode="voice",
            client=client,
        )

    audio = observed["session"]["audio"]
    assert audio["input"]["turn_detection"]["type"] == "server_vad"
    assert audio["input"]["transcription"] == {
        "model": "live-stt-deployment",
        "language": "en",
        "delay": "low",
    }


@pytest.mark.asyncio
async def test_voice_secret_requires_a_configured_live_transcription_deployment() -> (
    None
):
    settings = Settings(
        _env_file=None,
        azure_openai_endpoint="https://example.services.ai.azure.com",
        azure_openai_api_key="server-key",
        azure_openai_realtime_deployment="interviewer-deployment",
    )

    with pytest.raises(RealtimeServiceError) as caught:
        await create_realtime_client_secret(
            settings=settings,
            instructions="prompt",
            input_mode="voice",
        )

    assert caught.value.status_code == 503
    assert "input transcription" in str(caught.value).lower()


@pytest.mark.asyncio
async def test_client_secret_errors_do_not_return_provider_response_body() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "provider-secret-detail"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RealtimeServiceError) as caught:
            await create_realtime_client_secret(
                settings=_settings(),
                instructions="prompt",
                input_mode="text_dev",
                client=client,
            )

    assert "provider-secret-detail" not in str(caught.value)
    assert "credential" in str(caught.value)
