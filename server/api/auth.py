"""Clerk session identity extraction and local-development identity support."""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .database import get_database_session
from .models import DeletionReceipt, User

logger = logging.getLogger(__name__)

# Clerk signs session tokens with RS256. Anything else is a forgery attempt or a
# misconfiguration, so the allow-list stays closed.
_CLERK_ALGORITHMS = ("RS256",)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    subject: str
    email: str
    display_name: str


def clerk_frontend_api_origin(settings: Settings) -> str:
    """Resolve Clerk's Frontend API origin, which is also the token issuer.

    An explicit ``CLERK_FRONTEND_API_URL`` wins. Otherwise it is recovered from
    the publishable key, which is ``pk_(test|live)_`` followed by the
    base64-encoded origin with a trailing ``$``.
    """

    explicit = (settings.clerk_frontend_api_url or "").strip().rstrip("/")
    if explicit:
        return explicit

    key = (settings.clerk_publishable_key or "").strip()
    _, _, encoded = key.partition("_")
    _, _, encoded = encoded.partition("_")
    if encoded:
        try:
            padding = "=" * (-len(encoded) % 4)
            decoded = base64.b64decode(encoded + padding).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            decoded = ""
        host = decoded.rstrip("$").strip()
        if host:
            return f"https://{host}"

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=(
            "Clerk is misconfigured: set CLERK_FRONTEND_API_URL or a valid "
            "CLERK_PUBLISHABLE_KEY."
        ),
    )


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    # PyJWKClient caches fetched keys in-process, so the network hop happens
    # only on a cache miss. Setting CLERK_JWT_KEY avoids it entirely.
    return PyJWKClient(jwks_url, cache_keys=True)


def _signing_key(token: str, settings: Settings) -> str:
    pem = (settings.clerk_jwt_key or "").strip()
    if pem:
        return pem.replace("\\n", "\n")
    issuer = clerk_frontend_api_origin(settings)
    return (
        _jwks_client(f"{issuer}/.well-known/jwks.json")
        .get_signing_key_from_jwt(token)
        .key
    )


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    # Clerk's browser SDK also keeps the session in a first-party cookie.
    return request.cookies.get("__session") or None


def _clerk_principal(request: Request, settings: Settings) -> AuthenticatedPrincipal:
    token = _bearer_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in is required.",
        )

    issuer = clerk_frontend_api_origin(settings)
    try:
        claims = jwt.decode(
            token,
            _signing_key(token, settings),
            algorithms=list(_CLERK_ALGORITHMS),
            issuer=issuer,
            # Clerk does not set `aud` on the default session token.
            options={"verify_aud": False, "require": ["exp", "sub"]},
            leeway=5,
        )
    except jwt.PyJWTError as exc:
        logger.warning("invalid_clerk_session_token", exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The session was invalid or has expired.",
        ) from exc

    authorized_parties = settings.clerk_authorized_party_list
    azp = claims.get("azp")
    if authorized_parties and azp not in authorized_parties:
        logger.warning("clerk_authorized_party_rejected", extra={"azp": azp})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The session was issued for a different application.",
        )

    subject = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip()
    display_name = str(claims.get("name") or "").strip() or email
    if not subject or not email:
        # Clerk's default session token carries neither claim. They are added in
        # the dashboard under Sessions -> Customize session token:
        #   {"email": "{{user.primary_email_address}}", "name": "{{user.full_name}}"}
        logger.error("clerk_session_token_missing_claims")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "The session token did not contain a subject and email. Add "
                "email and name claims to the Clerk session token."
            ),
        )
    return AuthenticatedPrincipal(subject, email, display_name)


def principal_from_request(
    request: Request, settings: Settings
) -> AuthenticatedPrincipal:
    if settings.auth_mode == "local":
        return AuthenticatedPrincipal(
            subject=settings.local_auth_subject,
            email=settings.local_auth_email,
            display_name=settings.local_auth_name,
        )
    return _clerk_principal(request, settings)


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
