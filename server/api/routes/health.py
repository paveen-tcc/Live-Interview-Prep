"""Liveness and dependency-readiness endpoints."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> dict[str, str]:
    try:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is not ready.") from exc
    return {"status": "ready", "database": "ok"}
