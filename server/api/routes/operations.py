"""Private-alpha usage visibility and account privacy controls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, principal_from_request
from ..config import Settings
from ..database import get_database_session
from ..models import DeletionReceipt, UsageEvent, User
from ..operations_schemas import DeleteAccountRequest, UsageSummaryResponse
from ..services.privacy import delete_account_data, privacy_hash

router = APIRouter(prefix="/api", tags=["private alpha operations"])


@router.get("/operations/usage", response_model=UsageSummaryResponse)
async def usage_summary(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> UsageSummaryResponse:
    settings: Settings = request.app.state.settings
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    period_start = now - timedelta(days=30)
    rows = list(
        await database.execute(
            select(
                UsageEvent.kind,
                func.sum(UsageEvent.quantity),
                func.sum(UsageEvent.estimated_cost_microusd),
            )
            .where(
                UsageEvent.user_id == user.id,
                UsageEvent.created_at >= period_start,
            )
            .group_by(UsageEvent.kind)
        )
    )
    events = {str(kind): int(quantity or 0) for kind, quantity, _cost in rows}
    total_microusd = sum(int(cost or 0) for _kind, _quantity, cost in rows)
    daily_used = int(
        await database.scalar(
            select(func.count())
            .select_from(UsageEvent)
            .where(
                UsageEvent.user_id == user.id,
                UsageEvent.kind == "session_created",
                UsageEvent.created_at >= day_start,
            )
        )
        or 0
    )
    return UsageSummaryResponse(
        period_started_at=period_start,
        daily_interview_quota=settings.daily_interview_quota,
        daily_interviews_used=daily_used,
        events=events,
        estimated_cost_usd=f"{Decimal(total_microusd) / Decimal(1_000_000):.6f}",
        cost_status="estimated" if total_microusd else "unavailable",
        transcript_retention_days=settings.transcript_retention_days,
        delivery_metrics_retention_days=settings.delivery_metrics_retention_days,
    )


@router.delete("/account", status_code=204)
async def delete_account(
    payload: DeleteAccountRequest,
    request: Request,
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> Response:
    settings: Settings = request.app.state.settings
    principal = principal_from_request(request, settings)
    principal_hash = privacy_hash("principal", principal.subject)
    receipt = await database.scalar(
        select(DeletionReceipt).where(
            DeletionReceipt.kind == "account",
            DeletionReceipt.target_hash == principal_hash,
        )
    )
    if receipt is not None:
        return Response(status_code=204)
    user = await database.scalar(
        select(User).where(User.auth_subject == principal.subject)
    )
    if user is not None:
        await delete_account_data(database, user, principal_hash=principal_hash)
        await database.commit()
    return Response(status_code=204)
