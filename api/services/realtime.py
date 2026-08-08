"""Azure Realtime GA client-secret service with a narrow browser response."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)


class RealtimeServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class RealtimeClientSecret:
    value: str
    expires_at: int
    calls_url: str


def azure_resource_root(endpoint: str) -> str:
    normalized = endpoint.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RealtimeServiceError(
            "AZURE_OPENAI_ENDPOINT must be a complete HTTPS Azure resource URL.",
            status_code=503,
        )
    path = parsed.path.rstrip("/")
    if path.endswith("/openai/v1"):
        path = path[: -len("/openai/v1")]
    elif path:
        raise RealtimeServiceError(
            "AZURE_OPENAI_ENDPOINT must contain only the Azure resource host.",
            status_code=503,
        )
    return urlunsplit(("https", parsed.netloc, path, "", "")).rstrip("/")


async def create_realtime_client_secret(
    *,
    settings: Settings,
    instructions: str,
    input_mode: str,
    client: httpx.AsyncClient | None = None,
) -> RealtimeClientSecret:
    if not settings.realtime_configured:
        raise RealtimeServiceError(
            "Azure Realtime is not configured. Add the Realtime deployment name.",
            status_code=503,
        )
    if input_mode == "voice" and not settings.live_transcription_configured:
        raise RealtimeServiceError(
            "Azure Realtime input transcription is not configured. "
            "Add the live transcription deployment name.",
            status_code=503,
        )

    root = azure_resource_root(settings.azure_openai_endpoint or "")
    session: dict[str, object] = {
        "type": "realtime",
        "model": settings.azure_openai_realtime_deployment,
        "instructions": instructions,
        "output_modalities": ["audio"],
        "max_output_tokens": 900,
        "audio": {
            "output": {"voice": settings.azure_openai_realtime_voice},
        },
    }
    if input_mode == "voice":
        session["audio"] = {
            "input": {
                "noise_reduction": {"type": "near_field"},
                "transcription": {
                    "model": settings.azure_openai_realtime_transcription_model,
                    "language": settings.azure_openai_transcription_language,
                    "delay": settings.azure_openai_transcription_delay,
                },
                "turn_detection": {
                    "type": "server_vad",
                    "create_response": True,
                    "interrupt_response": True,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 550,
                },
            },
            "output": {"voice": settings.azure_openai_realtime_voice},
        }
    request_body = {
        "expires_after": {
            "anchor": "created_at",
            "seconds": settings.realtime_client_secret_ttl_seconds,
        },
        "session": session,
    }
    headers = {
        "api-key": settings.azure_openai_api_key.get_secret_value(),
        "Content-Type": "application/json",
    }
    owns_client = client is None
    resolved_client = client or httpx.AsyncClient(
        timeout=settings.azure_openai_realtime_timeout_seconds
    )
    try:
        response = await resolved_client.post(
            f"{root}/openai/v1/realtime/client_secrets",
            headers=headers,
            json=request_body,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException as exc:
        raise RealtimeServiceError(
            "Azure Realtime timed out while preparing the interview. Try again.",
            status_code=504,
        ) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        logger.warning("realtime_client_secret_failed", extra={"azure_status": status})
        if status in {401, 403}:
            message = "Azure Realtime rejected the server credential. Check access."
        elif status == 404:
            message = "The configured Azure Realtime deployment was not found."
        elif status == 429:
            message = "Azure Realtime is temporarily at capacity. Try again shortly."
        else:
            message = "Azure Realtime could not prepare the interview. Try again."
        raise RealtimeServiceError(message, status_code=502) from exc
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise RealtimeServiceError(
            "Azure Realtime could not prepare the interview. Try again."
        ) from exc
    finally:
        if owns_client:
            await resolved_client.aclose()

    value = payload.get("value")
    expires_at = payload.get("expires_at")
    if not isinstance(value, str) or not value or not isinstance(expires_at, int):
        raise RealtimeServiceError(
            "Azure Realtime returned an invalid temporary credential."
        )
    return RealtimeClientSecret(
        value=value,
        expires_at=expires_at,
        calls_url=f"{root}/openai/v1/realtime/calls?webrtcfilter=on",
    )
