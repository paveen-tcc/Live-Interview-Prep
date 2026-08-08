"""Safe server capability discovery."""

from fastapi import APIRouter, Request

from domain.interview import SUPPORTED_DURATIONS

from ..schemas import CapabilityResponse

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])


@router.get("", response_model=CapabilityResponse)
async def capabilities(request: Request) -> CapabilityResponse:
    return CapabilityResponse(
        text_dev_mode_enabled=request.app.state.settings.enable_text_dev_mode,
        realtime_configured=request.app.state.settings.realtime_configured,
        live_transcription_configured=(
            request.app.state.settings.live_transcription_configured
        ),
        final_transcription_configured=(
            request.app.state.settings.final_transcription_configured
        ),
        typed_answer_max_characters=(
            request.app.state.settings.typed_answer_max_characters
        ),
        supported_durations=list(SUPPORTED_DURATIONS),
    )
