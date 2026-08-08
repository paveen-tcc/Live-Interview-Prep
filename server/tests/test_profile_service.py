from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.config import Settings
from api.services.profile import (
    ProfileExtractionError,
    StructuredResumeClaim,
    StructuredResumeProfile,
    extract_candidate_profile,
)


class FakeResponses:
    def __init__(self, parsed: StructuredResumeProfile) -> None:
        self.parsed = parsed
        self.calls: list[dict[str, object]] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.parsed)


class FakeClient:
    def __init__(self, parsed: StructuredResumeProfile) -> None:
        self.responses = FakeResponses(parsed)


def _llm_settings() -> Settings:
    return Settings(
        app_env="test",
        profile_extraction_mode="llm",
        azure_openai_endpoint="https://resume-resource.services.ai.azure.com",
        azure_openai_api_key="test-key",
        azure_openai_text_deployment="gpt-5.6-luna",
    )


def _sources() -> list[dict[str, str]]:
    return [
        {
            "source_id": "resume:page:1",
            "label": "Resume page 1",
            "text": (
                "Dhanush Kandhasamy\nSoftware Developer\n"
                "Built payment APIs and reduced latency by 35%.\n"
                "Python, FastAPI, and PostgreSQL"
            ),
        }
    ]


@pytest.mark.asyncio
async def test_llm_profile_is_condensed_and_source_grounded() -> None:
    parsed = StructuredResumeProfile(
        headline="Software Developer",
        headline_source_id="resume:page:1",
        headline_supporting_quote="Software Developer",
        claims=[
            StructuredResumeClaim(
                category="experience",
                text="Reduced payment API latency by 35%.",
                source_id="resume:page:1",
                supporting_quote=("Built payment APIs and reduced latency by 35%."),
            ),
            StructuredResumeClaim(
                category="skill",
                text="Uses Python, FastAPI, and PostgreSQL.",
                source_id="resume:page:1",
                supporting_quote="Python, FastAPI, and PostgreSQL",
            ),
        ],
    )
    client = FakeClient(parsed)

    profile = await extract_candidate_profile(
        _sources(),
        settings=_llm_settings(),
        client=client,
    )

    assert profile.headline == "Software Developer"
    assert [claim.category for claim in profile.claims] == ["experience", "skill"]
    assert profile.claims[0].source.label == "Resume page 1"
    assert profile.extractor_version == ("azure:gpt-5.6-luna:resume-profile-v1")
    assert client.responses.calls[0]["model"] == "gpt-5.6-luna"
    assert client.responses.calls[0]["store"] is False


@pytest.mark.asyncio
async def test_llm_profile_rejects_an_unsupported_quote() -> None:
    parsed = StructuredResumeProfile(
        headline="Software Developer",
        headline_source_id="resume:page:1",
        headline_supporting_quote="Software Developer",
        claims=[
            StructuredResumeClaim(
                category="experience",
                text="Led a team of 20 engineers.",
                source_id="resume:page:1",
                supporting_quote="Led a team of 20 engineers.",
            )
        ],
    )

    with pytest.raises(ProfileExtractionError, match="could not be verified"):
        await extract_candidate_profile(
            _sources(),
            settings=_llm_settings(),
            client=FakeClient(parsed),
        )


@pytest.mark.asyncio
async def test_explicit_ai_upgrade_requires_configuration() -> None:
    settings = Settings(
        app_env="test",
        profile_extraction_mode="auto",
        azure_openai_endpoint=None,
        azure_openai_api_key=None,
    )

    with pytest.raises(ProfileExtractionError) as caught:
        await extract_candidate_profile(
            _sources(),
            settings=settings,
            require_llm=True,
        )

    assert caught.value.status_code == 503
