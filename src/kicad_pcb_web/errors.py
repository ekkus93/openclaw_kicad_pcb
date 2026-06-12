"""Web-facing error helpers."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from kicad_pcb.errors import KiCadError, ToolError, UserError

_CAMEL_CASE_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")

# Matches private absolute path substrings embedded anywhere in a string.
_PRIVATE_PATH_RE = re.compile(r"/(?:tmp|var/folders|private/var|home/[^/\s]+|Users/[^/\s]+)/\S*")


def _error_type_name(exc: KiCadError) -> str:
    """Return a stable public error-type name for one KiCad error."""

    return _CAMEL_CASE_BOUNDARY_RE.sub("_", exc.__class__.__name__).lower()


def _sanitize_path_text(value: str) -> str:
    """Redact private filesystem paths from public error text.

    Standalone absolute paths are replaced entirely. Embedded private path
    substrings (under /tmp, /home, /Users, /var/folders, /private/var) are
    replaced with a stable placeholder wherever they appear in longer strings.
    """
    if Path(value.strip()).is_absolute():
        return "<redacted-path>"
    return _PRIVATE_PATH_RE.sub("<redacted-path>", value)


def _sanitize_detail_value(value: object) -> object:
    """Make one public error-detail value safe for API responses."""

    if isinstance(value, Path):
        return _sanitize_path_text(str(value))
    if isinstance(value, str):
        return _sanitize_path_text(value)
    if isinstance(value, list):
        return [_sanitize_detail_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_detail_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_detail_value(item) for key, item in value.items()}
    return value


def _public_error_details(exc: KiCadError) -> dict[str, object]:
    """Build the sanitized public details payload for one KiCad error."""

    details: dict[str, object] = {}
    for key, value in exc.details.items():
        details[str(key)] = _sanitize_detail_value(value)

    for attr_name in ("hint", "issue_count", "line", "col"):
        attr_value = getattr(exc, attr_name, None)
        if attr_value is not None and attr_name not in details:
            details[attr_name] = _sanitize_detail_value(attr_value)

    path_value = getattr(exc, "path", None)
    if path_value is not None and "path" not in details:
        details["path"] = _sanitize_detail_value(path_value)

    return details


def kicad_error_to_payload(exc: KiCadError) -> dict[str, object]:
    """Convert a KiCad domain error to the public API payload."""

    return {
        "error": {
            "type": _error_type_name(exc),
            "code": getattr(exc, "code", None),
            "message": _sanitize_path_text(str(exc)),
            "details": _public_error_details(exc),
        }
    }


def user_error_to_payload(exc: UserError) -> dict[str, object]:
    """Convert a domain user error to the public API payload."""

    return kicad_error_to_payload(exc)


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


async def handle_kicad_error(_: Request, exc: Exception) -> JSONResponse:
    """FastAPI exception handler for KiCad domain errors."""

    if not isinstance(exc, KiCadError):
        return JSONResponse(status_code=500, content=unexpected_error_to_payload())

    status_code = 400
    if isinstance(exc, ToolError) and exc.code == "TOOL_ERROR":
        status_code = 503
    return JSONResponse(status_code=status_code, content=kicad_error_to_payload(exc))


async def handle_request_validation_error(_: Request, exc: Exception) -> JSONResponse:
    """FastAPI exception handler for request validation failures."""

    if not isinstance(exc, RequestValidationError):
        return JSONResponse(status_code=500, content=unexpected_error_to_payload())
    return JSONResponse(status_code=422, content=validation_error_to_payload(exc))


async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    """FastAPI exception handler for uncaught exceptions."""

    return JSONResponse(status_code=500, content=unexpected_error_to_payload())
