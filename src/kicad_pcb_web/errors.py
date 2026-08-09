"""Web-facing error helpers and typed web-service failures."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from kicad_pcb.errors import KiCadError, ToolError, UserError

LOGGER = logging.getLogger("uvicorn.error")
_CAMEL_CASE_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")
_PRIVATE_PATH_RE = re.compile(r"/(?:tmp|var/folders|private/var|home/[^/\s]+|Users/[^/\s]+)/\S*")
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?:[A-Z]:[\\/]|\\\\)[^\s,;\"']*")


def new_error_id() -> str:
    """Return a non-secret correlation identifier for one failed operation."""

    return f"err_{uuid4().hex[:16]}"


class WebServiceError(RuntimeError):
    """Typed safe failure raised by the web orchestration layer."""

    status_code = 500
    code = "WEB_SERVICE_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, object] | None = None,
        error_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details or {}
        self.error_id = error_id


class ResourceNotFoundError(WebServiceError):
    status_code = 404
    code = "RESOURCE_NOT_FOUND"


class ConflictError(WebServiceError):
    status_code = 409
    code = "WIZARD_STATE_CONFLICT"


class ResourceBusyError(ConflictError):
    code = "RESOURCE_BUSY"


class ProviderUnavailableError(WebServiceError):
    status_code = 503
    code = "LLM_PROVIDER_UNAVAILABLE"


class UpstreamProviderError(WebServiceError):
    status_code = 502
    code = "LLM_PROVIDER_FAILED"


class PersistenceError(WebServiceError):
    status_code = 500
    code = "PERSISTENCE_FAILED"


class PersistedStateError(PersistenceError):
    code = "PERSISTED_STATE_INVALID"


def _error_type_name(exc: KiCadError) -> str:
    return _CAMEL_CASE_BOUNDARY_RE.sub("_", exc.__class__.__name__).lower()


def _sanitize_path_text(value: str) -> str:
    stripped = value.strip()
    if Path(stripped).is_absolute() or _WINDOWS_ABSOLUTE_PATH_RE.fullmatch(stripped):
        return "<redacted-path>"
    sanitized = _PRIVATE_PATH_RE.sub("<redacted-path>", value)
    return _WINDOWS_ABSOLUTE_PATH_RE.sub("<redacted-path>", sanitized)


def _sanitize_detail_value(value: object) -> object:
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
    return {
        "error": {
            "type": _error_type_name(exc),
            "code": getattr(exc, "code", None),
            "message": _sanitize_path_text(str(exc)),
            "details": _public_error_details(exc),
        }
    }


def web_service_error_to_payload(exc: WebServiceError) -> dict[str, object]:
    details = {str(key): _sanitize_detail_value(value) for key, value in exc.details.items()}
    if exc.error_id is not None:
        details["error_id"] = exc.error_id
    return {
        "error": {
            "type": "web_service_error",
            "code": exc.code,
            "message": _sanitize_path_text(str(exc)),
            "details": details,
        }
    }


def user_error_to_payload(exc: UserError) -> dict[str, object]:
    return kicad_error_to_payload(exc)


def _safe_validation_errors(exc: RequestValidationError) -> list[dict[str, object]]:
    """Return allowlisted request-validation metadata without rejected input values."""

    errors: list[dict[str, object]] = []
    for item in exc.errors():
        safe: dict[str, object] = {
            "loc": [
                str(part) if not isinstance(part, int) else part for part in item.get("loc", ())
            ],
            "type": str(item.get("type") or "validation_error"),
            "msg": _sanitize_path_text(str(item.get("msg") or "Invalid value.")),
        }
        ctx = item.get("ctx")
        if isinstance(ctx, dict):
            safe["ctx"] = _sanitize_detail_value(ctx)
        errors.append(safe)
    return errors


def validation_error_to_payload(exc: RequestValidationError) -> dict[str, object]:
    return {
        "error": {
            "type": "request_validation_error",
            "code": "REQUEST_VALIDATION_ERROR",
            "message": "Request validation failed.",
            "details": {"errors": _safe_validation_errors(exc)},
        }
    }


def unexpected_error_to_payload(*, error_id: str | None = None) -> dict[str, object]:
    details: dict[str, object] = {}
    if error_id is not None:
        details["error_id"] = error_id
    return {
        "error": {
            "type": "internal_error",
            "code": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected internal error occurred.",
            "details": details,
        }
    }


async def handle_web_service_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, WebServiceError):
        return JSONResponse(status_code=500, content=unexpected_error_to_payload())
    return JSONResponse(status_code=exc.status_code, content=web_service_error_to_payload(exc))


async def handle_user_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, UserError):
        return JSONResponse(status_code=500, content=unexpected_error_to_payload())
    return JSONResponse(status_code=400, content=user_error_to_payload(exc))


async def handle_kicad_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, KiCadError):
        return JSONResponse(status_code=500, content=unexpected_error_to_payload())

    status_code = 400
    if isinstance(exc, ToolError):
        status_code = 503 if exc.code in {"TOOL_ERROR", "KICAD_CLI_MISSING"} else 400
    return JSONResponse(status_code=status_code, content=kicad_error_to_payload(exc))


async def handle_request_validation_error(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        return JSONResponse(status_code=500, content=unexpected_error_to_payload())
    return JSONResponse(status_code=422, content=validation_error_to_payload(exc))


async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    error_id = new_error_id()
    LOGGER.error(
        "uncaught web request failure",
        extra={"error_id": error_id, "error_type": type(exc).__name__},
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(status_code=500, content=unexpected_error_to_payload(error_id=error_id))
