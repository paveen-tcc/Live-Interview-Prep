"""Backend-engineering JD extraction and seniority-calibrated scorecards."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

from domain.intake import (
    JobRequirement,
    ScorecardCompetency,
    ScorecardDocument,
    Seniority,
    SourceReference,
)


@dataclass(frozen=True)
class CompetencyTemplate:
    name: str
    description: str
    keywords: tuple[str, ...]
    priority: int
    evidence: tuple[str, ...]
    questions: tuple[str, ...]


TEMPLATES = (
    CompetencyTemplate(
        "Backend and API engineering",
        "Designs maintainable backend services and clear, reliable API contracts.",
        ("api", "backend", "rest", "graphql", "microservice", "service"),
        6,
        (
            "A concrete service or API personally designed",
            "Trade-offs in boundaries, contracts, and failure handling",
        ),
        ("API design", "service architecture", "failure scenario"),
    ),
    CompetencyTemplate(
        "Data persistence and SQL",
        "Models data effectively and reasons about queries, transactions, "
        "and performance.",
        ("sql", "postgres", "mysql", "database", "data model", "transaction"),
        5,
        (
            "A schema or query decision",
            "Use of indexes, transactions, or consistency trade-offs",
        ),
        ("database design", "query performance", "data consistency"),
    ),
    CompetencyTemplate(
        "Testing, debugging, and reliability",
        "Builds confidence through testing, observability, and systematic "
        "failure diagnosis.",
        ("test", "debug", "reliability", "observability", "monitor", "incident"),
        5,
        (
            "A production failure investigated",
            "A test strategy tied to realistic risks",
        ),
        ("debugging", "testing strategy", "production incident"),
    ),
    CompetencyTemplate(
        "Ownership and collaboration",
        "Communicates decisions, works across roles, and takes responsibility "
        "for outcomes.",
        ("collaborat", "stakeholder", "ownership", "mentor", "communicat", "team"),
        4,
        (
            "A personally owned outcome",
            "A disagreement, feedback, or cross-team decision",
        ),
        ("project ownership", "collaboration", "learning and feedback"),
    ),
    CompetencyTemplate(
        "Python application development",
        "Uses Python effectively for production backend systems.",
        ("python", "django", "fastapi", "flask"),
        4,
        (
            "Production Python code personally delivered",
            "Language or framework trade-offs",
        ),
        ("Python design", "framework decisions", "runtime behavior"),
    ),
    CompetencyTemplate(
        "Java and JVM services",
        "Builds production services in Java or JVM frameworks.",
        ("java", "spring", "jvm", "kotlin"),
        4,
        (
            "A JVM service personally delivered",
            "Concurrency, framework, or runtime trade-offs",
        ),
        ("Java service design", "JVM behavior", "Spring architecture"),
    ),
    CompetencyTemplate(
        "Cloud and distributed systems",
        "Reasons about distributed failure modes and operates services in "
        "cloud environments.",
        ("aws", "azure", "gcp", "cloud", "kubernetes", "distributed", "kafka"),
        4,
        ("A deployed cloud system", "Handling of retries, scaling, or partial failure"),
        ("distributed systems", "cloud operations", "scaling trade-offs"),
    ),
    CompetencyTemplate(
        "Security and privacy",
        "Applies practical application-security and data-protection controls.",
        (
            "security",
            "oauth",
            "authentication",
            "authorization",
            "privacy",
            "encryption",
        ),
        3,
        (
            "A threat or abuse case addressed",
            "Authorization and data-protection decisions",
        ),
        ("threat modeling", "authorization", "secure design"),
    ),
)

SENIORITY_EXPECTATIONS: dict[Seniority, str] = {
    "junior": (
        "Explains fundamentals, implements scoped work with guidance, tests "
        "changes, and asks useful clarifying questions."
    ),
    "mid": (
        "Works independently on production features, evaluates trade-offs, "
        "debugs failures, and communicates decisions clearly."
    ),
    "senior": (
        "Shapes system boundaries, anticipates operational risks, leads "
        "trade-offs, and raises the effectiveness of other engineers."
    ),
}


def extract_job_requirements(raw_description: str) -> list[JobRequirement]:
    sources = _jd_sources(raw_description)
    requirements = []
    for template in TEMPLATES:
        matching = [
            source for source in sources if _matches(source.excerpt, template.keywords)
        ]
        if not matching:
            continue
        classification = _classification_for_sources(matching)
        source = matching[0]
        requirements.append(
            JobRequirement(
                id=_stable_id("requirement", template.name),
                name=template.name,
                classification=classification,
                source=source,
            )
        )
    return requirements


def generate_scorecard(
    raw_description: str, seniority: Seniority
) -> tuple[list[JobRequirement], ScorecardDocument]:
    sources = _jd_sources(raw_description)
    requirements = extract_job_requirements(raw_description)
    requirement_by_name = {item.name: item for item in requirements}
    core_names = {
        "Backend and API engineering",
        "Data persistence and SQL",
        "Testing, debugging, and reliability",
        "Ownership and collaboration",
    }
    selected = [template for template in TEMPLATES if template.name in core_names]
    selected.extend(
        template
        for template in TEMPLATES
        if template.name not in core_names and template.name in requirement_by_name
    )
    selected = selected[:7]
    weights = _normalized_weights([template.priority for template in selected])
    default_source = sources[0]
    competencies = []
    for template, weight in zip(selected, weights, strict=True):
        requirement = requirement_by_name.get(template.name)
        classification = requirement.classification if requirement else "trainable"
        source_reference = requirement.source if requirement else default_source
        competencies.append(
            ScorecardCompetency(
                id=_stable_id("competency", template.name),
                name=template.name,
                description=template.description,
                weight=weight,
                classification=classification,
                seniority_expectation=SENIORITY_EXPECTATIONS[seniority],
                evidence_to_collect=list(template.evidence),
                question_families=list(template.questions),
                source_references=[source_reference],
            )
        )
    return requirements, ScorecardDocument(competencies=competencies)


def _jd_sources(raw_description: str) -> list[SourceReference]:
    lines = [" ".join(line.split()) for line in raw_description.splitlines()]
    lines = [line for line in lines if line]
    if len(lines) == 1:
        sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", lines[0])]
        lines = [item for item in sentences if item]
    return [
        SourceReference(
            source_id=f"jd:line:{number}",
            label=f"Job description line {number}",
            excerpt=line[:500],
        )
        for number, line in enumerate(lines, start=1)
    ]


def _matches(value: str, keywords: tuple[str, ...]) -> bool:
    lowered = value.casefold()
    return any(keyword in lowered for keyword in keywords)


def _classification_for_sources(sources: list[SourceReference]) -> str:
    text = " ".join(source.excerpt for source in sources).casefold()
    if any(token in text for token in ("must", "required", "minimum", "essential")):
        return "must-have"
    if any(token in text for token in ("bonus", "preferred", "nice to have", "plus")):
        return "nice-to-have"
    return "trainable"


def _normalized_weights(priorities: list[int]) -> list[int]:
    total = sum(priorities)
    raw = [priority * 100 / total for priority in priorities]
    weights = [math.floor(value) for value in raw]
    remaining = 100 - sum(weights)
    order = sorted(
        range(len(raw)), key=lambda index: raw[index] - weights[index], reverse=True
    )
    for index in order[:remaining]:
        weights[index] += 1
    return weights


def _stable_id(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()[:12]
    return f"{kind}-{digest}"
