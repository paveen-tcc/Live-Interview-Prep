"""Bounded Azure final-transcription client for candidate audio."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

import httpx

from ..config import Settings
from .realtime import RealtimeServiceError, azure_resource_root

_MAX_ATTEMPTS = 3
_PROMPT_MAX_CHARS = 2_000
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]{1,63}")
_COMMON_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "built",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "the",
    "to",
    "using",
    "with",
}


@dataclass(frozen=True)
class FinalTranscription:
    text: str
    deployment: str
    elapsed_ms: int
    attempts: int


class TranscriptionServiceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int = 502,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.attempts = attempts


def build_transcription_prompt(snapshot: Mapping[str, object]) -> str:
    """Build short, source-backed recognition hints from frozen setup data."""

    target = _mapping(snapshot.get("target"))
    role_title = _clean_text(target.get("title"))[:256]
    evidence = _mapping(snapshot.get("candidate_profile")).get("evidence")
    terms = _claim_terms(evidence)

    prefix = f"Role: {role_title}." if role_title else "Role: interview candidate."
    if not terms:
        return prefix[:_PROMPT_MAX_CHARS]

    prompt = f"{prefix} Terms: "
    for term in terms:
        candidate = (
            f"{prompt}{term}, " if not prompt.endswith(" ") else f"{prompt}{term}"
        )
        if len(candidate.rstrip(", ")) > _PROMPT_MAX_CHARS:
            break
        prompt = candidate
    return prompt.rstrip(", ")[:_PROMPT_MAX_CHARS]


async def transcribe_candidate_audio(
    *,
    settings: Settings,
    audio: bytes,
    media_type: str,
    filename: str,
    prompt: str,
    client: httpx.AsyncClient | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> FinalTranscription:
    """Return sanitized Azure transcript output or raise a categorized error."""

    if not settings.final_transcription_configured:
        raise TranscriptionServiceError(
            "Azure final transcription is not configured.",
            code="transcription_not_configured",
            status_code=503,
        )

    deployment = (settings.azure_openai_final_transcription_deployment or "").strip()
    try:
        root = azure_resource_root(settings.azure_openai_endpoint or "")
    except RealtimeServiceError as exc:
        raise TranscriptionServiceError(
            "Azure final transcription is not configured.",
            code="transcription_not_configured",
            status_code=503,
        ) from exc

    key = settings.azure_openai_api_key.get_secret_value()
    headers = {"api-key": key}
    # Azure AI Foundry resources serve audio transcription from the
    # deployment-scoped route. The unified /openai/v1/audio/transcriptions
    # surface answers DeploymentNotFound even when the deployment exists, which
    # would degrade every candidate answer to its live transcript.
    url = (
        f"{root}/openai/deployments/{deployment}/audio/transcriptions"
        f"?api-version={settings.azure_openai_transcription_api_version}"
    )
    data = {
        "model": deployment,
        "language": settings.azure_openai_transcription_language,
        "prompt": prompt[:_PROMPT_MAX_CHARS],
    }
    files = {"file": (filename, audio, media_type)}
    owns_client = client is None
    resolved_client = client or httpx.AsyncClient(
        timeout=settings.azure_openai_final_transcription_timeout_seconds
    )
    started = time.perf_counter()

    try:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = await resolved_client.post(
                    url,
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=settings.azure_openai_final_transcription_timeout_seconds,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt < _MAX_ATTEMPTS:
                    await sleep(_retry_delay(attempt))
                    continue
                raise _transport_error(exc, attempt) from exc

            if response.status_code >= 400:
                if (
                    _is_retryable_status(response.status_code)
                    and attempt < _MAX_ATTEMPTS
                ):
                    await sleep(_retry_delay(attempt))
                    continue
                raise _status_error(response.status_code, attempt)

            try:
                payload = response.json()
            except (ValueError, TypeError) as exc:
                raise TranscriptionServiceError(
                    "Azure final transcription returned an invalid response.",
                    code="transcription_invalid_response",
                    attempts=attempt,
                ) from exc

            text = _clean_text(
                payload.get("text") if isinstance(payload, dict) else None
            )
            if not text:
                raise TranscriptionServiceError(
                    "Azure final transcription returned no transcript.",
                    code="transcription_empty",
                    attempts=attempt,
                )
            return FinalTranscription(
                text=text,
                deployment=deployment,
                elapsed_ms=int((time.perf_counter() - started) * 1_000),
                attempts=attempt,
            )
    finally:
        if owns_client:
            await resolved_client.aclose()

    raise AssertionError("transcription retry loop did not return or raise")


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _claim_terms(evidence: object) -> list[str]:
    if not isinstance(evidence, list):
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for item in evidence:
        text = _clean_text(_mapping(item).get("text"))
        for term in _TOKEN.findall(text):
            normalized = term.casefold()
            if normalized in _COMMON_WORDS or normalized in seen:
                continue
            seen.add(normalized)
            terms.append(term)
    return terms


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(_CONTROL_CHARACTERS.sub(" ", value).split())


def _retry_delay(attempt: int) -> float:
    return 0.25 * (2 ** (attempt - 1))


def _is_retryable_status(status: int) -> bool:
    return status in {408, 429} or 500 <= status <= 599


def _transport_error(exc: Exception, attempts: int) -> TranscriptionServiceError:
    if isinstance(exc, httpx.TimeoutException):
        return TranscriptionServiceError(
            "Azure final transcription timed out. Try again.",
            code="transcription_timeout",
            status_code=504,
            attempts=attempts,
        )
    return TranscriptionServiceError(
        "Azure final transcription is temporarily unavailable. Try again.",
        code="transcription_unavailable",
        attempts=attempts,
    )


def _status_error(status: int, attempts: int) -> TranscriptionServiceError:
    if status in {401, 403}:
        return TranscriptionServiceError(
            "Azure final transcription rejected the server credential.",
            code="transcription_auth",
            status_code=503,
            attempts=attempts,
        )
    if status == 404:
        return TranscriptionServiceError(
            "The configured final transcription deployment was not found.",
            code="transcription_deployment_missing",
            status_code=503,
            attempts=attempts,
        )
    if status == 413:
        return TranscriptionServiceError(
            "The recorded answer is too large to transcribe.",
            code="transcription_too_large",
            status_code=413,
            attempts=attempts,
        )
    if _is_retryable_status(status):
        return TranscriptionServiceError(
            "Azure final transcription is temporarily unavailable. Try again.",
            code="transcription_unavailable",
            attempts=attempts,
        )
    return TranscriptionServiceError(
        "Azure final transcription rejected the audio request.",
        code="transcription_request_failed",
        attempts=attempts,
    )
