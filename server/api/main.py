"""FastAPI application factory and React static-build host."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .database import (
    create_database,
    create_schema,
    ensure_sqlite_parent_directory,
    install_database_network_compatibility,
)
from .errors import install_error_handlers
from .logging_config import configure_logging
from .routes import (
    auth,
    capabilities,
    delivery,
    evaluations,
    health,
    intake,
    interviews,
    operations,
    realtime,
)

logger = logging.getLogger(__name__)

# Clerk serves its browser SDK from the instance's Frontend API origin. Every
# development instance lives under this wildcard; a production instance on a
# custom domain is added through CLERK_FRONTEND_API_URL.
_CLERK_DEV_ORIGIN = "https://*.clerk.accounts.dev"
_CLERK_IMAGE_ORIGIN = "https://img.clerk.com"
# Clerk's bot protection renders a Cloudflare Turnstile widget in an iframe.
_CLERK_TURNSTILE_ORIGIN = "https://challenges.cloudflare.com"


def content_security_policy(settings: Settings) -> str:
    script_src = ["'self'"]
    connect_src = ["'self'", "https://*.services.ai.azure.com"]
    img_src = ["'self'", "data:"]
    frame_src = ["'none'"]

    if settings.auth_mode == "clerk":
        clerk_origins = [_CLERK_DEV_ORIGIN]
        explicit = (settings.clerk_frontend_api_url or "").strip().rstrip("/")
        if explicit and explicit not in clerk_origins:
            clerk_origins.append(explicit)
        script_src.extend([*clerk_origins, _CLERK_TURNSTILE_ORIGIN])
        connect_src.extend(clerk_origins)
        img_src.append(_CLERK_IMAGE_ORIGIN)
        frame_src = [_CLERK_TURNSTILE_ORIGIN]

    return "; ".join(
        [
            "default-src 'self'",
            f"script-src {' '.join(script_src)}",
            "style-src 'self'",
            "font-src 'self'",
            f"img-src {' '.join(img_src)}",
            f"connect-src {' '.join(connect_src)}",
            "media-src 'self' blob:",
            "worker-src 'self' blob:",
            f"frame-src {' '.join(frame_src)}",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        install_database_network_compatibility(resolved_settings.database_url)
        ensure_sqlite_parent_directory(resolved_settings.database_url)
        engine, session_factory = create_database(resolved_settings)
        application.state.engine = engine
        application.state.session_factory = session_factory
        if resolved_settings.auto_create_schema:
            await create_schema(engine)
        logger.info(
            "application_started",
            extra={"app_env": resolved_settings.app_env},
        )
        try:
            yield
        finally:
            await engine.dispose()
            logger.info("application_stopped")

    application = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if resolved_settings.app_env != "production" else None,
        redoc_url=None,
        openapi_url=(
            "/api/openapi.json" if resolved_settings.app_env != "production" else None
        ),
    )
    application.state.settings = resolved_settings
    application.state.realtime_secret_attempts = {}
    application.state.realtime_secret_cache = {}
    install_error_handlers(application)
    policy = content_security_policy(resolved_settings)

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": elapsed_ms,
                },
            )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), geolocation=(), microphone=(self)"
        )
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = policy
        if resolved_settings.app_env in {"staging", "production"}:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    application.include_router(health.router)
    application.include_router(auth.router)
    application.include_router(capabilities.router)
    application.include_router(interviews.router)
    application.include_router(realtime.router)
    application.include_router(delivery.router)
    application.include_router(evaluations.router)
    application.include_router(intake.router)
    application.include_router(operations.router)

    dist_directory = resolved_settings.web_dist_dir
    assets_directory = dist_directory / "assets"
    if assets_directory.is_dir():
        application.mount(
            "/assets",
            StaticFiles(directory=assets_directory),
            name="web-assets",
        )

    @application.get("/{path:path}", include_in_schema=False)
    async def react_application(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint was not found.")
        index_file = dist_directory / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        if path:
            raise HTTPException(status_code=404, detail="Page was not found.")
        return JSONResponse(
            status_code=200,
            content={
                "name": resolved_settings.app_name,
                "message": "React build not found. Run the Vite development server.",
            },
        )

    return application


app = create_app()
