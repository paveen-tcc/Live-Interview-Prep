"""M2 ownership-scoped resume, profile, JD, and scorecard endpoints."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from domain.intake import (
    CandidateProfileDocument,
    ScorecardCompetency,
    ScorecardDocument,
)
from domain.interview import SETUP_FROZEN_STATES

from ..auth import get_current_user
from ..config import Settings
from ..database import get_database_session
from ..intake_schemas import (
    CandidateProfileResponse,
    CandidateProfileUpdateRequest,
    CreateJobTargetRequest,
    ExtractCandidateProfileRequest,
    GenerateScorecardRequest,
    InterviewSetupResponse,
    JobTargetResponse,
    ScorecardResponse,
    UpdateScorecardRequest,
    UploadResponse,
)
from ..models import (
    CandidateProfile,
    InterviewSession,
    JobTarget,
    Scorecard,
    Upload,
    User,
)
from ..services.profile import ProfileExtractionError, extract_candidate_profile
from ..services.scorecards import extract_job_requirements, generate_scorecard
from ..services.uploads import UploadValidationError, validate_and_extract

logger = logging.getLogger(__name__)
router = APIRouter(tags=["intake"])


async def _owned_interview(
    database: AsyncSession, user: User, interview_id: str
) -> InterviewSession:
    interview = await database.scalar(
        select(InterviewSession).where(
            InterviewSession.id == interview_id,
            InterviewSession.user_id == user.id,
        )
    )
    if interview is None:
        raise HTTPException(status_code=404, detail="Practice session was not found.")
    return interview


async def _assert_setup_editable(
    database: AsyncSession,
    *,
    profile_id: str | None = None,
    scorecard_id: str | None = None,
) -> None:
    conditions = [InterviewSession.status.in_(SETUP_FROZEN_STATES)]
    if profile_id is not None:
        conditions.append(InterviewSession.profile_id == profile_id)
    elif scorecard_id is not None:
        conditions.append(InterviewSession.scorecard_id == scorecard_id)
    else:
        return
    frozen = await database.scalar(select(InterviewSession.id).where(*conditions))
    if frozen is not None:
        raise HTTPException(
            status_code=409,
            detail="Interview setup is frozen after the interview starts.",
        )


def _profile_response(profile: CandidateProfile) -> CandidateProfileResponse:
    document = CandidateProfileDocument.model_validate(profile.structured_claims)
    return CandidateProfileResponse(
        id=profile.id,
        source_resume_id=profile.source_resume_id,
        headline=document.headline,
        claims=document.claims,
        extractor_version=document.extractor_version,
        version=profile.version,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _job_target_response(job_target: JobTarget) -> JobTargetResponse:
    return JobTargetResponse(
        id=job_target.id,
        title=job_target.title,
        seniority=job_target.seniority,
        raw_description=job_target.raw_description,
        structured_requirements=job_target.structured_requirements,
        created_at=job_target.created_at,
        updated_at=job_target.updated_at,
    )


def _scorecard_response(scorecard: Scorecard) -> ScorecardResponse:
    document = ScorecardDocument.model_validate(
        {"competencies": scorecard.competencies}
    )
    return ScorecardResponse(
        id=scorecard.id,
        job_target_id=scorecard.job_target_id,
        version=scorecard.version,
        competencies=document.competencies,
        total_weight=scorecard.total_weight,
        created_at=scorecard.created_at,
        updated_at=scorecard.updated_at,
    )


@router.post("/api/uploads/resume", response_model=UploadResponse, status_code=201)
async def upload_resume(
    request: Request,
    interview_id: Annotated[str, Form()],
    file: Annotated[UploadFile, File(description="PDF or DOCX resume")],
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> Upload:
    await _owned_interview(database, user, interview_id)
    settings: Settings = request.app.state.settings
    try:
        contents = await file.read(settings.resume_max_bytes + 1)
    finally:
        await file.close()
    try:
        extracted = await asyncio.wait_for(
            run_in_threadpool(
                validate_and_extract,
                filename=file.filename or "",
                media_type=file.content_type,
                contents=contents,
                settings=settings,
            ),
            timeout=settings.resume_extraction_timeout_seconds,
        )
    except TimeoutError as exc:
        logger.info("resume_upload_rejected", extra={"reason": "extraction_timeout"})
        raise HTTPException(
            status_code=422,
            detail=(
                "The resume took too long to process. Try a simpler exported document."
            ),
        ) from exc
    except UploadValidationError as exc:
        logger.info(
            "resume_upload_rejected",
            extra={"reason": type(exc).__name__, "status_code": exc.status_code},
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    now = datetime.now(UTC)
    upload = Upload(
        user_id=user.id,
        original_filename=(file.filename or "resume")[:255],
        file_type=extracted.file_type,
        media_type=extracted.media_type,
        size=extracted.size,
        sha256=extracted.sha256,
        extracted_text=extracted.text,
        source_segments=extracted.segments,
        retention_expires_at=now,
        raw_deleted_at=now,
    )
    database.add(upload)
    await database.commit()
    await database.refresh(upload)
    return upload


@router.post(
    "/api/candidate-profiles/extract",
    response_model=CandidateProfileResponse,
    status_code=201,
)
async def create_candidate_profile(
    request: Request,
    payload: ExtractCandidateProfileRequest,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> CandidateProfileResponse:
    interview = await _owned_interview(database, user, payload.interview_id)
    upload = await database.scalar(
        select(Upload).where(Upload.id == payload.upload_id, Upload.user_id == user.id)
    )
    if upload is None:
        raise HTTPException(status_code=404, detail="Resume upload was not found.")

    profile = await database.scalar(
        select(CandidateProfile).where(
            CandidateProfile.source_resume_id == upload.id,
            CandidateProfile.user_id == user.id,
        )
    )
    if profile is not None and not payload.replace_existing:
        document = None
    else:
        if profile is not None:
            await _assert_setup_editable(database, profile_id=profile.id)
            current = CandidateProfileDocument.model_validate(profile.structured_claims)
            if any(claim.edited for claim in current.claims):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This profile contains saved corrections. AI re-extraction "
                        "was stopped so those edits are not overwritten."
                    ),
                )
        settings: Settings = request.app.state.settings
        try:
            document = await extract_candidate_profile(
                upload.source_segments,
                settings=settings,
                require_llm=payload.replace_existing,
            )
        except ProfileExtractionError as exc:
            logger.warning(
                "resume_profile_extraction_failed",
                extra={"status_code": exc.status_code},
            )
            raise HTTPException(
                status_code=exc.status_code,
                detail=str(exc),
            ) from exc

    if profile is None:
        assert document is not None
        profile = CandidateProfile(
            user_id=user.id,
            source_resume_id=upload.id,
            structured_claims=document.model_dump(mode="json"),
        )
        database.add(profile)
        await database.flush()
    elif document is not None:
        profile.structured_claims = document.model_dump(mode="json")
        profile.version += 1
    interview.profile_id = profile.id
    if interview.status == "DRAFT":
        interview.status = "PROFILE_READY"
    await database.commit()
    await database.refresh(profile)
    return _profile_response(profile)


@router.patch(
    "/api/candidate-profiles/{profile_id}", response_model=CandidateProfileResponse
)
async def update_candidate_profile(
    profile_id: str,
    payload: CandidateProfileUpdateRequest,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> CandidateProfileResponse:
    profile = await database.scalar(
        select(CandidateProfile).where(
            CandidateProfile.id == profile_id,
            CandidateProfile.user_id == user.id,
        )
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Candidate profile was not found.")
    await _assert_setup_editable(database, profile_id=profile.id)
    current = CandidateProfileDocument.model_validate(profile.structured_claims)
    current_by_id = {claim.id: claim for claim in current.claims}
    if {claim.id for claim in payload.claims} != set(current_by_id):
        raise HTTPException(
            status_code=409,
            detail="The profile changed since it was loaded. Refresh before saving.",
        )
    updated_claims = []
    for edit in payload.claims:
        existing = current_by_id[edit.id]
        changed = edit.text != existing.text
        updated_claims.append(
            existing.model_copy(
                update={
                    "text": edit.text,
                    "edited": existing.edited or changed,
                    "original_text": (
                        existing.original_text or existing.text
                        if existing.edited or changed
                        else None
                    ),
                }
            )
        )
    updated = CandidateProfileDocument(
        headline=payload.headline,
        claims=updated_claims,
        extractor_version=current.extractor_version,
    )
    profile.structured_claims = updated.model_dump(mode="json")
    profile.version += 1
    await database.commit()
    await database.refresh(profile)
    return _profile_response(profile)


@router.post("/api/job-targets", response_model=JobTargetResponse, status_code=201)
async def create_job_target(
    payload: CreateJobTargetRequest,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> JobTargetResponse:
    interview = await _owned_interview(database, user, payload.interview_id)
    if interview.status in SETUP_FROZEN_STATES:
        raise HTTPException(
            status_code=409,
            detail="Interview setup is frozen after the interview starts.",
        )
    requirements = extract_job_requirements(payload.raw_description)
    target = JobTarget(
        user_id=user.id,
        title=payload.title,
        seniority=payload.seniority,
        raw_description=payload.raw_description,
        structured_requirements=[item.model_dump(mode="json") for item in requirements],
    )
    database.add(target)
    await database.commit()
    await database.refresh(target)
    return _job_target_response(target)


@router.post(
    "/api/scorecards/generate", response_model=ScorecardResponse, status_code=201
)
async def create_scorecard(
    payload: GenerateScorecardRequest,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ScorecardResponse:
    interview = await _owned_interview(database, user, payload.interview_id)
    if interview.status in SETUP_FROZEN_STATES:
        raise HTTPException(
            status_code=409,
            detail="Interview setup is frozen after the interview starts.",
        )
    if interview.scorecard_id:
        existing = await database.get(Scorecard, interview.scorecard_id)
        if existing is not None:
            return _scorecard_response(existing)
    target = await database.scalar(
        select(JobTarget).where(
            JobTarget.id == payload.job_target_id,
            JobTarget.user_id == user.id,
        )
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Job target was not found.")
    requirements, document = generate_scorecard(
        target.raw_description, target.seniority
    )
    target.structured_requirements = [
        item.model_dump(mode="json") for item in requirements
    ]
    scorecard = Scorecard(
        job_target_id=target.id,
        competencies=[item.model_dump(mode="json") for item in document.competencies],
        total_weight=100,
    )
    database.add(scorecard)
    await database.flush()
    interview.scorecard_id = scorecard.id
    interview.status = "SCORECARD_READY"
    if interview.title == "Untitled practice session":
        interview.title = f"{target.title} practice"[:120]
    await database.commit()
    await database.refresh(scorecard)
    return _scorecard_response(scorecard)


@router.patch("/api/scorecards/{scorecard_id}", response_model=ScorecardResponse)
async def update_scorecard(
    scorecard_id: str,
    payload: UpdateScorecardRequest,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> ScorecardResponse:
    scorecard = await database.scalar(
        select(Scorecard)
        .join(JobTarget, Scorecard.job_target_id == JobTarget.id)
        .where(Scorecard.id == scorecard_id, JobTarget.user_id == user.id)
    )
    if scorecard is None:
        raise HTTPException(status_code=404, detail="Scorecard was not found.")
    await _assert_setup_editable(database, scorecard_id=scorecard.id)
    current = ScorecardDocument.model_validate({"competencies": scorecard.competencies})
    current_by_id = {item.id: item for item in current.competencies}
    if {item.id for item in payload.competencies} != set(current_by_id):
        raise HTTPException(
            status_code=409,
            detail="The scorecard changed since it was loaded. Refresh before saving.",
        )
    competencies = [
        ScorecardCompetency(
            **edit.model_dump(),
            source_references=current_by_id[edit.id].source_references,
        )
        for edit in payload.competencies
    ]
    document = ScorecardDocument(
        competencies=competencies,
        generator_version=current.generator_version,
    )
    scorecard.competencies = [
        item.model_dump(mode="json") for item in document.competencies
    ]
    scorecard.total_weight = 100
    scorecard.version += 1
    await database.commit()
    await database.refresh(scorecard)
    return _scorecard_response(scorecard)


@router.get(
    "/api/interviews/{interview_id}/setup", response_model=InterviewSetupResponse
)
async def get_interview_setup(
    interview_id: str,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> InterviewSetupResponse:
    interview = await _owned_interview(database, user, interview_id)
    profile = (
        await database.get(CandidateProfile, interview.profile_id)
        if interview.profile_id
        else None
    )
    upload = await database.get(Upload, profile.source_resume_id) if profile else None
    scorecard = (
        await database.get(Scorecard, interview.scorecard_id)
        if interview.scorecard_id
        else None
    )
    target = (
        await database.get(JobTarget, scorecard.job_target_id) if scorecard else None
    )
    return InterviewSetupResponse(
        upload=UploadResponse.model_validate(upload) if upload else None,
        profile=_profile_response(profile) if profile else None,
        job_target=_job_target_response(target) if target else None,
        scorecard=_scorecard_response(scorecard) if scorecard else None,
    )
