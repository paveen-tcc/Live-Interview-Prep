"""Async SQLAlchemy engine and request-scoped session helpers."""

from __future__ import annotations

import asyncio
import socket
import ssl
from collections.abc import AsyncIterator
from pathlib import Path
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

# Managed PostgreSQL providers that terminate TLS themselves and hand out URLs
# carrying libpq-style query options asyncpg does not understand.
_MANAGED_POSTGRES_SUFFIXES = (".neon.tech", ".supabase.co", ".supabase.com")

# Supabase serves its database and pooler endpoints from a private CA rather
# than a publicly trusted one, so certifi's roots reject them. Their public root
# certificate is vendored here so verify-full works with no extra setup;
# DATABASE_SSL_ROOT_CERT overrides it.
_SUPABASE_SUFFIXES = (".supabase.co", ".supabase.com")
SUPABASE_ROOT_CA = (
    Path(__file__).resolve().parents[1] / "certs" / ("supabase-prod-ca-2021.crt")
)

# Supabase's Supavisor pooler runs transaction pooling on 6543 and session
# pooling on 5432. Transaction pooling multiplexes one server connection across
# clients, so server-side prepared statements cannot be reused.
_SUPAVISOR_TRANSACTION_PORT = 6543


def _is_managed_postgres_host(hostname: str | None) -> bool:
    return bool(hostname and hostname.endswith(_MANAGED_POSTGRES_SUFFIXES))


def normalized_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parsed = urlsplit(database_url)
    if _is_managed_postgres_host(parsed.hostname):
        query = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if key in {"channel_binding", "sslmode", "pgbouncer"}:
                # SQLAlchemy passes URI query options to asyncpg as keyword
                # arguments. asyncpg accepts `ssl`, not `sslmode`, and treats
                # channel_binding as a server setting. Engine construction adds
                # a verified SSLContext instead.
                continue
            query.append((key, value))
        return urlunsplit(parsed._replace(query=urlencode(query)))

    return database_url


def uses_transaction_pooler(database_url: str) -> bool:
    parsed = urlsplit(normalized_database_url(database_url))
    return (
        _is_managed_postgres_host(parsed.hostname)
        and parsed.port == _SUPAVISOR_TRANSACTION_PORT
    )


def database_ca_bundle(hostname: str | None, ssl_root_cert: object | None) -> str:
    """Choose the CA bundle that verifies the given database host."""

    if ssl_root_cert:
        return str(ssl_root_cert)
    if hostname and hostname.endswith(_SUPABASE_SUFFIXES):
        return str(SUPABASE_ROOT_CA)
    return certifi.where()


def database_connect_args(
    database_url: str,
    timeout_seconds: float = 10.0,
    ssl_root_cert: object | None = None,
) -> dict[str, object]:
    normalized = normalized_database_url(database_url)
    if normalized.startswith("sqlite"):
        return {"check_same_thread": False}
    hostname = urlsplit(normalized).hostname
    if not _is_managed_postgres_host(hostname):
        return {}
    connect_args: dict[str, object] = {
        "ssl": ssl.create_default_context(
            cafile=database_ca_bundle(hostname, ssl_root_cert)
        ),
        "timeout": timeout_seconds,
    }
    if uses_transaction_pooler(database_url):
        # Without this asyncpg reuses prepared statement names across pooled
        # server connections and Supavisor answers with DuplicatePreparedStatement.
        connect_args["statement_cache_size"] = 0
    return connect_args


def ensure_sqlite_parent_directory(database_url: str) -> None:
    """Create the directory holding a SQLite file so first boot can write it."""

    normalized = normalized_database_url(database_url)
    if not normalized.startswith("sqlite"):
        return
    _, _, location = normalized.partition("///")
    location = location.split("?", 1)[0]
    if location and location != ":memory:":
        Path(location).parent.mkdir(parents=True, exist_ok=True)


def database_engine_options(database_url: str) -> dict[str, object]:
    """Dialect-level engine options for the given database URL."""

    if uses_transaction_pooler(database_url):
        # SQLAlchemy keeps its own asyncpg prepared-statement cache on top of
        # asyncpg's; both have to be off behind a transaction pooler.
        return {"prepared_statement_cache_size": 0}
    return {}


def install_database_network_compatibility(
    database_url: str, *, loop: Any | None = None
) -> None:
    """Prefer IPv4 for Neon while preserving its hostname for verified TLS.

    Deliberately Neon-only. Supabase's direct connection endpoint resolves to
    IPv6 exclusively, so pinning AF_INET there would break it outright; reach
    Supabase through the IPv4-capable Supavisor pooler instead.
    """
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
            ssl_root_cert=settings.database_ssl_root_cert,
        ),
        **database_engine_options(settings.database_url),
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
