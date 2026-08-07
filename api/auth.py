"""Managed-auth identity extraction and local-development identity support."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .database import get_database_session
from .models import DeletionReceipt, User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    subject: str
    email: str
    display_name: str


def _claim(claims: dict[str, str], *names: str) -> str | None:
    for name in names:
        value = claims.get(name)
        if value:
            return value
    return None


def _email_claim(claims: dict[str, str]) -> str | None:
    value = _claim(
        claims,
        "email",
        "emails",
        "preferred_username",
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    )
    if not value:
        return None
    if value.startswith("["):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(decoded, list) and decoded and isinstance(decoded[0], str):
            return decoded[0]
        return None
    return value


def _decode_easy_auth_principal(encoded: str) -> dict[str, str]:
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = base64.b64decode(encoded + padding, validate=True)
        principal = json.loads(payload)
        return {
            str(item["typ"]): str(item["val"])
            for item in principal.get("claims", [])
            if "typ" in item and "val" in item
        }
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        logger.warning("invalid_easy_auth_principal", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The managed authentication identity was invalid.",
        ) from exc


def principal_from_request(
    request: Request, settings: Settings
) -> AuthenticatedPrincipal:
    if settings.auth_mode == "local":
        return AuthenticatedPrincipal(
            subject=settings.local_auth_subject,
            email=settings.local_auth_email,
            display_name=settings.local_auth_name,
        )

    encoded = request.headers.get("x-ms-client-principal")
    header_subject = request.headers.get("x-ms-client-principal-id")
    header_name = request.headers.get("x-ms-client-principal-name")
    if not encoded and not header_subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in is required.",
        )

    claims = _decode_easy_auth_principal(encoded) if encoded else {}
    subject = header_subject or _claim(
        claims,
        "http://schemas.microsoft.com/identity/claims/objectidentifier",
        "oid",
        "sub",
    )
    email = _email_claim(claims) or header_name
    display_name = (
        _claim(
            claims,
            "name",
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
        )
        or email
    )
    if not subject or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The managed identity did not contain a subject and email.",
        )
    return AuthenticatedPrincipal(subject, email, display_name or email)


async def get_current_user(
    request: Request,
    database: Annotated[AsyncSession, Depends(get_database_session)],
) -> User:
    settings: Settings = request.app.state.settings
    principal = principal_from_request(request, settings)
    principal_hash = hashlib.sha256(
        f"principal:{principal.subject}".encode()
    ).hexdigest()
    deleted = await database.scalar(
        select(DeletionReceipt.id).where(
            DeletionReceipt.kind == "account",
            DeletionReceipt.target_hash == principal_hash,
        )
    )
    if deleted is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This account and its retained data were deleted.",
        )
    user = await database.scalar(
        select(User).where(User.auth_subject == principal.subject)
    )
    if user is None:
        user = User(
            auth_subject=principal.subject,
            email=principal.email,
            display_name=principal.display_name,
        )
        database.add(user)
    else:
        user.email = principal.email
        user.display_name = principal.display_name
    await database.commit()
    await database.refresh(user)
    return user
