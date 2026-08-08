"""Source-grounded candidate-profile extraction."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from domain.intake import CandidateProfileDocument, ProfileClaim, SourceReference
from prompts.resume_profile_v1 import PROMPT_VERSION, SYSTEM_PROMPT

from ..config import Settings

logger = logging.getLogger(__name__)

SECTION_CATEGORIES = {
    "summary": "summary",
    "profile": "summary",
    "professional summary": "summary",
    "skills": "skill",
    "technical skills": "skill",
    "technologies": "skill",
    "experience": "experience",
    "work experience": "experience",
    "professional experience": "experience",
    "employment": "experience",
    "projects": "experience",
    "education": "education",
    "certifications": "education",
}
CONTACT_PATTERN = re.compile(
    r"(?:@|https?://|linkedin\.com|github\.com|\+?\d[\d ()-]{7,})", re.I
)
BULLET_PREFIX = re.compile(r"^[\s•●▪◦*\-–—]+")
ClaimCategory = Literal["summary", "skill", "experience", "education", "other"]


class ProfileExtractionError(RuntimeError):
    """A safe, user-displayable profile extraction failure."""

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class StructuredResumeClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: ClaimCategory
    text: str
    source_id: str
    supporting_quote: str


class StructuredResumeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str
    headline_source_id: str
    headline_supporting_quote: str
    claims: list[StructuredResumeClaim]


async def extract_candidate_profile(
    source_segments: list[dict[str, str]],
    *,
    settings: Settings,
    require_llm: bool = False,
    client: Any | None = None,
) -> CandidateProfileDocument:
    """Extract once with the LLM when configured, otherwise use local rules."""

    use_llm = settings.profile_extraction_mode == "llm" or (
        settings.profile_extraction_mode == "auto"
        and settings.app_env != "test"
        and settings.llm_profile_configured
    )
    if require_llm and not settings.llm_profile_configured:
        raise ProfileExtractionError(
            "AI résumé extraction is not configured. Set the Azure OpenAI "
            "endpoint, API key, and text deployment.",
            status_code=503,
        )
    if require_llm:
        use_llm = True
    if not use_llm:
        return extract_candidate_profile_rules(source_segments)
    return await _extract_candidate_profile_llm(
        source_segments,
        settings=settings,
        client=client,
    )


async def _extract_candidate_profile_llm(
    source_segments: list[dict[str, str]],
    *,
    settings: Settings,
    client: Any | None = None,
) -> CandidateProfileDocument:
    prepared_segments = _prepare_segments(
        source_segments,
        settings.resume_llm_max_input_characters,
    )
    if not prepared_segments:
        raise ProfileExtractionError("The résumé did not contain extractable text.")

    resolved_client = client or AsyncOpenAI(
        api_key=settings.azure_openai_api_key.get_secret_value(),
        base_url=_azure_v1_base_url(settings.azure_openai_endpoint or ""),
        timeout=settings.resume_llm_timeout_seconds,
        max_retries=0,
    )
    user_input = json.dumps(
        {"resume_sources": prepared_segments},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    try:
        response = await asyncio.wait_for(
            resolved_client.responses.parse(
                model=settings.azure_openai_text_deployment,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_input},
                ],
                text_format=StructuredResumeProfile,
                store=False,
            ),
            timeout=settings.resume_llm_timeout_seconds,
        )
    except TimeoutError as exc:
        raise ProfileExtractionError(
            "AI résumé extraction timed out. Try again in a moment.",
            status_code=504,
        ) from exc
    except Exception as exc:
        logger.warning(
            "resume_llm_request_failed",
            extra={"error_type": type(exc).__name__},
        )
        raise ProfileExtractionError(
            "AI résumé extraction could not reach the configured text deployment. "
            "Check the deployment name and try again.",
            status_code=502,
        ) from exc

    parsed = response.output_parsed
    if parsed is None:
        raise ProfileExtractionError(
            "The AI could not produce a structured résumé profile. Try again."
        )
    return _validate_llm_profile(
        parsed,
        prepared_segments,
        settings.azure_openai_text_deployment,
    )


def _validate_llm_profile(
    parsed: StructuredResumeProfile,
    source_segments: list[dict[str, str]],
    deployment: str,
) -> CandidateProfileDocument:
    sources = {segment["source_id"]: segment for segment in source_segments}
    _require_supported_quote(
        parsed.headline_source_id,
        parsed.headline_supporting_quote,
        sources,
    )
    headline = " ".join(parsed.headline.split()).strip()
    if not headline or len(headline) > 240:
        raise ProfileExtractionError(
            "The AI returned an invalid résumé headline. Try again."
        )

    claims: list[ProfileClaim] = []
    seen: set[str] = set()
    for item in parsed.claims:
        source = _require_supported_quote(
            item.source_id,
            item.supporting_quote,
            sources,
        )
        text = " ".join(item.text.split()).strip()
        fingerprint = text.casefold()
        if len(text) < 3 or len(text) > 2_000 or fingerprint in seen:
            continue
        seen.add(fingerprint)
        quote = " ".join(item.supporting_quote.split()).strip()
        claims.append(
            ProfileClaim(
                id=_stable_id(item.source_id, text, item.category),
                category=item.category,
                text=text,
                source=SourceReference(
                    source_id=item.source_id,
                    label=source["label"],
                    excerpt=quote[:320],
                ),
            )
        )

    if not claims:
        raise ProfileExtractionError(
            "The AI did not return any source-supported résumé evidence. Try again."
        )
    return CandidateProfileDocument(
        headline=headline,
        claims=claims[:80],
        extractor_version=f"azure:{deployment}:{PROMPT_VERSION}",
    )


def _require_supported_quote(
    source_id: str,
    quote: str,
    sources: dict[str, dict[str, str]],
) -> dict[str, str]:
    source = sources.get(source_id)
    normalized_quote = _normalize_for_support(quote)
    if (
        source is None
        or len(normalized_quote) < 3
        or normalized_quote not in _normalize_for_support(source["text"])
    ):
        raise ProfileExtractionError(
            "The AI returned résumé evidence that could not be verified against "
            "the uploaded document. Try again."
        )
    return source


def _prepare_segments(
    source_segments: list[dict[str, str]], max_characters: int
) -> list[dict[str, str]]:
    remaining = max(1, max_characters)
    prepared: list[dict[str, str]] = []
    for segment in source_segments:
        if remaining <= 0:
            break
        text = segment["text"][:remaining].strip()
        if not text:
            continue
        prepared.append(
            {
                "source_id": segment["source_id"],
                "label": segment["label"],
                "text": text,
            }
        )
        remaining -= len(text)
    return prepared


def _azure_v1_base_url(endpoint: str) -> str:
    normalized = endpoint.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ProfileExtractionError(
            "AZURE_OPENAI_ENDPOINT must be a complete HTTPS Azure resource URL.",
            status_code=503,
        )
    if parsed.path.rstrip("/").endswith("/openai/v1"):
        return f"{normalized}/"
    if parsed.path.rstrip("/").endswith("/openai"):
        return f"{normalized}/v1/"
    return f"{normalized}/openai/v1/"


def _normalize_for_support(value: str) -> str:
    return " ".join(value.split()).casefold()


def extract_candidate_profile_rules(
    source_segments: list[dict[str, str]],
) -> CandidateProfileDocument:
    """Deterministic fallback used without LLM configuration and in tests."""

    claims: list[ProfileClaim] = []
    headline = ""
    category = "other"

    for segment in source_segments:
        source_id = segment["source_id"]
        label = segment["label"]
        for raw_line in segment["text"].splitlines():
            line = " ".join(BULLET_PREFIX.sub("", raw_line).split()).strip()
            if not line:
                continue
            heading_key = line.rstrip(":").casefold()
            if heading_key in SECTION_CATEGORIES:
                category = SECTION_CATEGORIES[heading_key]
                continue
            if not headline and not CONTACT_PATTERN.search(line) and len(line) <= 240:
                headline = line
                continue
            if CONTACT_PATTERN.search(line) or len(line) < 3:
                continue

            claim_category: ClaimCategory = category  # type: ignore[assignment]
            if claim_category == "other" and _looks_like_skill_list(line):
                claim_category = "skill"
            claims.append(
                ProfileClaim(
                    id=_stable_id(source_id, line, claim_category),
                    category=claim_category,
                    text=line,
                    source=SourceReference(
                        source_id=source_id,
                        label=label,
                        excerpt=line[:320],
                    ),
                )
            )
            if len(claims) >= 80:
                break
        if len(claims) >= 80:
            break

    if not headline:
        headline = "Candidate profile"
    if not claims:
        first = source_segments[0]
        excerpt = " ".join(first["text"].split())[:320]
        claims.append(
            ProfileClaim(
                id=_stable_id(first["source_id"], excerpt, "summary"),
                category="summary",
                text=excerpt,
                source=SourceReference(
                    source_id=first["source_id"],
                    label=first["label"],
                    excerpt=excerpt,
                ),
            )
        )
    return CandidateProfileDocument(headline=headline, claims=claims)


def _looks_like_skill_list(value: str) -> bool:
    separators = value.count(",") + value.count("|") + value.count("•")
    return separators >= 2 and len(value) <= 300


def _stable_id(source_id: str, text: str, category: str) -> str:
    digest = hashlib.sha256(f"{source_id}:{category}:{text}".encode()).hexdigest()[:12]
    return f"claim-{digest}"
