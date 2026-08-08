"""Environment-backed application configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "interview_coach.db"
ENV_FILES = (PROJECT_ROOT / ".env", PROJECT_ROOT / ".env.local")


class Settings(BaseSettings):
    app_name: str = "AI Interview Coach"
    app_env: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"
    database_url: str = f"sqlite+aiosqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"
    database_connect_timeout_seconds: float = 10.0
    auto_create_schema: bool = True
    auth_mode: Literal["local", "easy_auth"] = "local"
    local_auth_subject: str = "local-developer"
    local_auth_email: str = "developer@local.test"
    local_auth_name: str = "Local developer"
    web_dist_dir: Path = PROJECT_ROOT / "web" / "dist"
    enable_text_dev_mode: bool = False
    typed_answer_max_characters: int = 20_000
    realtime_client_secret_ttl_seconds: int = 120
    realtime_client_secret_rate_limit: int = 6
    realtime_reconnect_window_seconds: int = 180
    daily_interview_quota: int = 10
    daily_evaluation_quota: int = 20
    transcript_retention_days: int = 30
    draft_retention_days: int = 30
    delivery_metrics_retention_days: int = 30
    usage_event_retention_days: int = 90
    resume_max_bytes: int = 5_000_000
    resume_max_pages: int = 10
    resume_max_extracted_characters: int = 200_000
    resume_max_docx_entries: int = 500
    resume_max_docx_uncompressed_bytes: int = 20_000_000
    resume_extraction_timeout_seconds: float = 8.0
    profile_extraction_mode: Literal["auto", "rules", "llm"] = "auto"
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_text_deployment: str = "gpt-5.6-luna"
    azure_openai_realtime_deployment: str | None = None
    azure_openai_realtime_voice: str = "marin"
    azure_openai_realtime_transcription_model: str = "gpt-4o-mini-transcribe"
    azure_openai_final_transcription_deployment: str | None = None
    azure_openai_transcription_language: str = "en"
    azure_openai_transcription_delay: Literal[
        "minimal", "low", "medium", "high", "xhigh"
    ] = "low"
    azure_openai_realtime_timeout_seconds: float = 20.0
    azure_openai_final_transcription_timeout_seconds: float = 30.0
    azure_openai_final_transcription_max_bytes: int = 25_000_000
    resume_llm_timeout_seconds: float = 45.0
    resume_llm_max_input_characters: int = 80_000
    evaluation_llm_timeout_seconds: float = 90.0
    evaluation_max_transcript_characters: int = 160_000

    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_environment_boundaries(self) -> Settings:
        if self.app_env in {"staging", "production"}:
            if not self.database_url.startswith(
                ("postgresql+asyncpg://", "postgresql://")
            ):
                raise ValueError(
                    "DATABASE_URL must be PostgreSQL in staging and production."
                )
            if self.auth_mode != "easy_auth":
                raise ValueError(
                    "AUTH_MODE must be easy_auth in staging and production."
                )
            if self.auto_create_schema:
                raise ValueError(
                    "AUTO_CREATE_SCHEMA must be false in staging and production."
                )
        return self

    @property
    def llm_profile_configured(self) -> bool:
        key = (
            self.azure_openai_api_key.get_secret_value().strip()
            if self.azure_openai_api_key
            else ""
        )
        return bool(
            (self.azure_openai_endpoint or "").strip()
            and key
            and self.azure_openai_text_deployment.strip()
        )

    @property
    def text_model_configured(self) -> bool:
        return self.llm_profile_configured

    @property
    def realtime_configured(self) -> bool:
        key = (
            self.azure_openai_api_key.get_secret_value().strip()
            if self.azure_openai_api_key
            else ""
        )
        return bool(
            (self.azure_openai_endpoint or "").strip()
            and key
            and (self.azure_openai_realtime_deployment or "").strip()
        )

    @property
    def live_transcription_configured(self) -> bool:
        key = (
            self.azure_openai_api_key.get_secret_value().strip()
            if self.azure_openai_api_key
            else ""
        )
        return bool(
            (self.azure_openai_endpoint or "").strip()
            and key
            and self.azure_openai_realtime_transcription_model.strip()
        )

    @property
    def final_transcription_configured(self) -> bool:
        key = (
            self.azure_openai_api_key.get_secret_value().strip()
            if self.azure_openai_api_key
            else ""
        )
        return bool(
            (self.azure_openai_endpoint or "").strip()
            and key
            and (self.azure_openai_final_transcription_deployment or "").strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
