"""Current managed-auth user endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends

from ..auth import get_current_user
from ..models import User
from ..schemas import UserResponse

router = APIRouter(prefix="/api/auth", tags=["authentication"])


@router.get("/me", response_model=UserResponse)
async def current_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    return user
