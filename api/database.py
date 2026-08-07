"""Async SQLAlchemy engine and request-scoped session helpers."""

from __future__ import annotations

import asyncio
import socket
import ssl
from collections.abc import AsyncIterator
from typing import Any
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

_NEON_IPV4_ADAPTER_HOSTS = "_interview_coach_neon_ipv4_hosts"


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


def install_database_network_compatibility(
    database_url: str, *, loop: Any | None = None
) -> None:
    """Prefer IPv4 for Neon while preserving its hostname for verified TLS."""
    hostname = urlsplit(normalized_database_url(database_url)).hostname
    if not hostname or not hostname.endswith(".neon.tech"):
        return

    active_loop = loop or asyncio.get_running_loop()
    adapted_hosts = set(getattr(active_loop, _NEON_IPV4_ADAPTER_HOSTS, set()))
    if hostname in adapted_hosts:
        return

    original_create_connection = active_loop.create_connection

    async def create_connection(
        protocol_factory,
        host: str | None = None,
        port: int | None = None,
        **kwargs: object,
    ):
        if host == hostname and kwargs.get("family", socket.AF_UNSPEC) in {
            socket.AF_UNSPEC,
            0,
        }:
            kwargs["family"] = socket.AF_INET
        return await original_create_connection(protocol_factory, host, port, **kwargs)

    active_loop.create_connection = create_connection
    adapted_hosts.add(hostname)
    setattr(active_loop, _NEON_IPV4_ADAPTER_HOSTS, adapted_hosts)


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
