"""Web-facing error helpers."""

from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from kicad_pcb.errors import UserError


def user_error_to_payload(exc: UserError) -> dict[str, object]:
    """Convert a domain user error to the public API payload."""

    return {
        "error": {
            "type": "user_error",
            "code": getattr(exc, "code", None),
            "message": str(exc),
            "details": getattr(exc, "details", {}) or {},
        }
    }


def validation_error_to_payload(exc: RequestValidationError) -> dict[str, object]:
    """Convert FastAPI validation errors to a stable payload."""

    return {
        "error": {
            "type": "request_validation_error",
            "code": "REQUEST_VALIDATION_ERROR",
            "message": "Request validation failed.",
            "details": {"errors": exc.errors()},
        }
    }


def unexpected_error_to_payload() -> dict[str, object]:
    """Return a generic 500 payload without leaking internal paths."""

    return {
        "error": {
            "type": "internal_error",
            "code": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected server error occurred.",
            "details": {},
        }
    }


async def handle_user_error(_: Request, exc: Exception) -> JSONResponse:
    """FastAPI exception handler for domain user errors."""

    if not isinstance(exc, UserError):
        return JSONResponse(status_code=500, content=unexpected_error_to_payload())
    return JSONResponse(status_code=400, content=user_error_to_payload(exc))


async def handle_request_validation_error(_: Request, exc: Exception) -> JSONResponse:
    """FastAPI exception handler for request validation failures."""

    if not isinstance(exc, RequestValidationError):
        return JSONResponse(status_code=500, content=unexpected_error_to_payload())
    return JSONResponse(status_code=422, content=validation_error_to_payload(exc))


async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    """FastAPI exception handler for uncaught exceptions."""

    return JSONResponse(status_code=500, content=unexpected_error_to_payload())
