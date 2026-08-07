"""FastAPI application factory and React static-build host."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .database import (
    create_database,
    create_schema,
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


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        install_database_network_compatibility(resolved_settings.database_url)
        engine, session_factory = create_database(resolved_settings)
        application.state.engine = engine
        application.state.session_factory = session_factory
        if resolved_settings.auto_create_schema:
            Path("data").mkdir(parents=True, exist_ok=True)
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
        response.headers["Content-Security-Policy"] = "; ".join(
            [
                "default-src 'self'",
                "script-src 'self'",
                "style-src 'self'",
                "font-src 'self'",
                "img-src 'self' data:",
                "connect-src 'self' https://*.services.ai.azure.com",
                "media-src 'self' blob:",
                "worker-src 'self' blob:",
                "object-src 'none'",
                "base-uri 'self'",
                "form-action 'self'",
                "frame-ancestors 'none'",
            ]
        )
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
