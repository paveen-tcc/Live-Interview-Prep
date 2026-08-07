"""Consistent public errors with support-friendly identifiers."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def _error_response(
    *,
    status_code: int,
    error_id: str,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        headers={"X-Error-ID": error_id, **(headers or {})},
        content={
            "error": {
                "id": error_id,
                "code": code,
                "message": message,
            }
        },
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def handle_http_error(
        request: Request, exception: HTTPException
    ) -> JSONResponse:
        error_id = str(uuid.uuid4())
        return _error_response(
            status_code=exception.status_code,
            error_id=error_id,
            code="http_error",
            message=str(exception.detail),
            headers=exception.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exception: RequestValidationError
    ) -> JSONResponse:
        error_id = str(uuid.uuid4())
        logger.info(
            "request_validation_failed",
            extra={
                "error_id": error_id,
                "request_id": getattr(request.state, "request_id", None),
                "validation_errors": [
                    {"type": error["type"], "location": error["loc"]}
                    for error in exception.errors()
                ],
            },
        )
        return _error_response(
            status_code=422,
            error_id=error_id,
            code="validation_error",
            message="The request did not pass validation.",
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request, exception: Exception
    ) -> JSONResponse:
        error_id = str(uuid.uuid4())
        logger.exception(
            "unhandled_request_error",
            exc_info=exception,
            extra={
                "error_id": error_id,
                "request_id": getattr(request.state, "request_id", None),
            },
        )
        return _error_response(
            status_code=500,
            error_id=error_id,
            code="internal_error",
            message="Something went wrong. Use the error ID when asking for help.",
        )
