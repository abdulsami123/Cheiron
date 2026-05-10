from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .logging_config import get_logger

log = get_logger(__name__)


class AppError(Exception):
    """Base error with HTTP mapping."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.retry_after = retry_after


class UpstreamNotFound(AppError):
    status_code = 404
    code = "upstream_not_found"


class UpstreamBadRequest(AppError):
    status_code = 400
    code = "upstream_bad_request"


class UpstreamUnavailable(AppError):
    """5xx, timeout, or malformed JSON from an upstream."""

    status_code = 502
    code = "upstream_unavailable"


class LLMPlanningError(AppError):
    status_code = 502
    code = "llm_planning_failed"


def _payload(err: AppError) -> dict[str, Any]:
    body: dict[str, Any] = {
        "error": {
            "code": err.code,
            "message": err.message,
        }
    }
    if err.details:
        body["error"]["details"] = err.details
    if err.retry_after is not None:
        body["error"]["retry_after"] = err.retry_after
    return body


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    log.warning("app_error code=%s status=%s msg=%s", exc.code, exc.status_code, exc.message)
    headers = {}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(status_code=exc.status_code, content=_payload(exc), headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
