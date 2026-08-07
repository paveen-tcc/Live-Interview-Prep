"""Async SQLAlchemy engine and request-scoped session helpers."""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import certifi
from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import Settings
from .models import Base


def normalized_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parsed = urlsplit(database_url)
    if parsed.hostname and parsed.hostname.endswith(".neon.tech"):
        query = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if key in {"channel_binding", "sslmode"}:
                # SQLAlchemy passes URI query options to asyncpg as keyword
                # arguments. asyncpg accepts `ssl`, not `sslmode`, and treats
                # channel_binding as a server setting. Engine construction adds
                # a verified SSLContext for Neon instead.
                continue
            query.append((key, value))
        return urlunsplit(parsed._replace(query=urlencode(query)))

    return database_url


def database_connect_args(
    database_url: str, timeout_seconds: float = 10.0
) -> dict[str, object]:
    normalized = normalized_database_url(database_url)
    if normalized.startswith("sqlite"):
        return {"check_same_thread": False}
    hostname = urlsplit(normalized).hostname
    if hostname and hostname.endswith(".neon.tech"):
        return {
            "ssl": ssl.create_default_context(cafile=certifi.where()),
            "timeout": timeout_seconds,
        }
    return {}


def create_database(
    settings: Settings,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    url = normalized_database_url(settings.database_url)
    engine = create_async_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args=database_connect_args(
            settings.database_url,
            timeout_seconds=settings.database_connect_timeout_seconds,
        ),
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] = (
        request.app.state.session_factory
    )
    async with session_factory() as session:
        yield session
