"""Validated configuration shared by the local desktop prototype scripts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
import re
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit


_API_VERSION_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})(?P<preview>-preview)?$"
)


class ConfigurationError(ValueError):
    """A missing or invalid desktop-prototype setting."""


@dataclass(frozen=True)
class AzureRealtimeSettings:
    api_key: str
    endpoint: str
    api_version: str
    deployment: str
    voice: str


def normalize_azure_endpoint(endpoint: str) -> str:
    """Return the Azure resource root expected by ``AsyncAzureOpenAI``."""
    value = endpoint.strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(
            "AZURE_OPENAI_ENDPOINT must be a complete HTTPS Azure resource URL "
            "without credentials, a query, or a fragment."
        )

    path = parsed.path.rstrip("/")
    if path.lower().endswith("/openai/v1"):
        path = path[: -len("/openai/v1")]
    elif path:
        raise ConfigurationError(
            "AZURE_OPENAI_ENDPOINT must contain only the Azure resource host; "
            "remove extra URL paths."
        )
    return urlunsplit(("https", parsed.netloc, path, "", "")).rstrip("/")


def validate_api_version(api_version: str) -> str:
    """Validate Azure's date-based API version format and calendar date."""
    value = api_version.strip()
    match = _API_VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise ConfigurationError(
            "AZURE_OPENAI_API_VERSION must use YYYY-MM-DD or "
            "YYYY-MM-DD-preview format (for example, 2025-04-01-preview)."
        )
    try:
        date.fromisoformat(match.group("date"))
    except ValueError as exc:
        raise ConfigurationError(
            "AZURE_OPENAI_API_VERSION contains an invalid calendar date."
        ) from exc
    return value


def load_azure_realtime_settings(
    environment: Mapping[str, str] | None = None,
) -> AzureRealtimeSettings:
    """Load and validate all Azure settings before opening local devices."""
    env = os.environ if environment is None else environment
    raw = {
        "AZURE_OPENAI_API_KEY": env.get("AZURE_OPENAI_API_KEY", "").strip(),
        "AZURE_OPENAI_ENDPOINT": env.get("AZURE_OPENAI_ENDPOINT", "").strip(),
        "AZURE_OPENAI_API_VERSION": env.get(
            "AZURE_OPENAI_API_VERSION", ""
        ).strip(),
        "AZURE_OPENAI_REALTIME_DEPLOYMENT": (
            env.get("AZURE_OPENAI_REALTIME_DEPLOYMENT", "").strip()
            or env.get("AZURE_OPENAI_DEPLOYMENT_NAME", "").strip()
        ),
    }
    missing = [name for name, value in raw.items() if not value]
    if missing:
        raise ConfigurationError(
            "Missing Azure configuration: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill every Azure value."
        )

    voice = env.get("AZURE_OPENAI_REALTIME_VOICE", "marin").strip()
    if not voice:
        raise ConfigurationError(
            "AZURE_OPENAI_REALTIME_VOICE must not be blank when it is set."
        )

    deployment = raw["AZURE_OPENAI_REALTIME_DEPLOYMENT"]
    if any(character.isspace() for character in deployment):
        raise ConfigurationError(
            "AZURE_OPENAI_REALTIME_DEPLOYMENT must be a deployment name, not a URL "
            "or a value containing whitespace."
        )

    return AzureRealtimeSettings(
        api_key=raw["AZURE_OPENAI_API_KEY"],
        endpoint=normalize_azure_endpoint(raw["AZURE_OPENAI_ENDPOINT"]),
        api_version=validate_api_version(raw["AZURE_OPENAI_API_VERSION"]),
        deployment=deployment,
        voice=voice,
    )
