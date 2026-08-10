"""Wizard session file I/O: persistence, reads, and state helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from kicad_pcb.errors import UserError

from ..errors import PersistedStateError, PersistenceError
from ..settings import WebSettings
from ..wizard_models import (
    WizardFailureKind,
    WizardMessage,
    WizardMessageRole,
    WizardSessionDetail,
)
from .atomic_io import atomic_write_json

LOGGER = logging.getLogger("uvicorn.error")
_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_DEBUG_ARTIFACT_MAX_FILES_PER_STAGE = 20
_DEBUG_ARTIFACT_MAX_TOTAL_BYTES = 25 * 1024 * 1024
_DEBUG_ARTIFACT_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "attempt",
        "response_model",
        "messages",
        "completion_error",
        "completion",
        "parse_error",
        "parsed",
    }
)
_DEBUG_ARTIFACT_MESSAGE_FIELDS: frozenset[str] = frozenset({"role", "content"})
_DEBUG_ARTIFACT_COMPLETION_FIELDS: frozenset[str] = frozenset(
    {"provider", "model", "content", "finish_reason", "request_id", "outcome"}
)
_DEBUG_ARTIFACT_ERROR_FIELDS: frozenset[str] = frozenset({"code", "message"})


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _wizard_root(settings: WebSettings) -> Path:
    path = settings.data_dir / "wizard_sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _new_session_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"wiz_{stamp}_{uuid4().hex[:8]}"


def _session_dir(settings: WebSettings, session_id: str) -> Path:
    if not _SAFE_SESSION_ID_RE.fullmatch(session_id):
        raise UserError(f"Unsafe wizard session id: {session_id!r}")
    return _wizard_root(settings) / session_id


def _session_json_path(settings: WebSettings, session_id: str) -> Path:
    return _session_dir(settings, session_id) / "wizard.json"


def _unlink_derived(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise PersistenceError(
            "Failed to remove stale derived wizard state.",
            details={"filename": path.name, "error_type": type(exc).__name__},
        ) from exc


def _refresh_derived_exports(session_dir: Path, session: WizardSessionDetail) -> None:
    """Refresh non-authoritative convenience exports from one canonical revision."""

    spec_path = session_dir / "spec.json"
    ir_path = session_dir / "circuit_ir.json"
    if session.spec is None:
        _unlink_derived(spec_path)
    else:
        atomic_write_json(spec_path, session.spec.model_dump(mode="json"))

    if session.ir_json is None:
        _unlink_derived(ir_path)
    else:
        atomic_write_json(ir_path, session.ir_json)

    atomic_write_json(
        session_dir / "derived_state.json",
        {
            "authoritative_file": "wizard.json",
            "wizard_updated_at": session.updated_at,
            "spec_present": session.spec is not None,
            "ir_present": session.ir_json is not None,
        },
    )


def _persist_session(settings: WebSettings, session: WizardSessionDetail) -> WizardSessionDetail:
    """Commit canonical state before refreshing derived convenience exports.

    ``wizard.json`` is the only authoritative state. If a derived export fails
    after that atomic commit, the error records that the canonical revision is
    already durable; callers must refetch it rather than attempt rollback.
    """

    session_dir = _session_dir(settings, session.id)
    session_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_session_json_path(settings, session.id), session.model_dump(mode="json"))
    try:
        _refresh_derived_exports(session_dir, session)
    except PersistenceError as exc:
        LOGGER.error(
            "derived wizard export refresh failed after authoritative commit",
            extra={
                "session_id": session.id,
                "wizard_updated_at": session.updated_at,
                "error_code": exc.code,
            },
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        raise PersistenceError(
            "Authoritative wizard state was committed, but derived exports could not be refreshed.",
            code="DERIVED_WIZARD_EXPORT_FAILED",
            details={
                "session_id": session.id,
                "authoritative_committed": True,
                "wizard_updated_at": session.updated_at,
            },
        ) from exc
    return session


def _debug_artifact_dir(settings: WebSettings, session_id: str) -> Path:
    return _session_dir(settings, session_id) / "debug_artifacts"


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "posix":
        return
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise PersistenceError(
            "Failed to secure wizard debug artifact directory.",
            details={"directory": path.name, "error_type": type(exc).__name__},
        ) from exc


def _debug_artifact_order_key(path: Path) -> tuple[str, str]:
    parts = path.name.split("_", 2)
    timestamp = parts[1] if len(parts) > 2 else path.name
    return timestamp, path.name


def _unlink_debug_artifact(path: Path) -> bool:
    try:
        path.unlink()
    except OSError as exc:
        LOGGER.warning(
            "failed to prune wizard debug artifact",
            extra={"artifact_name": path.name, "error_type": type(exc).__name__},
        )
        return False
    return True


def _prune_debug_artifacts(artifact_dir: Path, *, stage: str, newest: Path) -> None:
    stage_files = sorted(artifact_dir.glob(f"{stage}_*.json"), key=_debug_artifact_order_key)
    candidates = [path for path in stage_files if path != newest]
    excess = max(0, len(stage_files) - _DEBUG_ARTIFACT_MAX_FILES_PER_STAGE)
    for path in candidates[:excess]:
        _unlink_debug_artifact(path)

    all_files = sorted(artifact_dir.glob("*.json"), key=_debug_artifact_order_key)
    sizes: dict[Path, int] = {}
    total_bytes = 0
    for path in all_files:
        try:
            size = path.stat().st_size
        except OSError as exc:
            LOGGER.warning(
                "failed to inspect wizard debug artifact during pruning",
                extra={"artifact_name": path.name, "error_type": type(exc).__name__},
            )
            continue
        sizes[path] = size
        total_bytes += size

    if total_bytes <= _DEBUG_ARTIFACT_MAX_TOTAL_BYTES:
        return
    for path in all_files:
        if path == newest or total_bytes <= _DEBUG_ARTIFACT_MAX_TOTAL_BYTES:
            continue
        size_to_remove = sizes.get(path)
        if size_to_remove is None:
            continue
        if _unlink_debug_artifact(path):
            total_bytes -= size_to_remove


def _require_debug_string_or_none(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"unsafe debug artifact type for {field_name}")
    return value


def _debug_prompt_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, list):
        raise ValueError("unsafe debug artifact type for messages")

    canonical_messages: list[dict[str, str]] = []
    prompt_chars = 0
    for message in value:
        if not isinstance(message, dict) or set(message) - _DEBUG_ARTIFACT_MESSAGE_FIELDS:
            raise ValueError("unsafe debug artifact message shape")
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            raise ValueError("unsafe debug artifact message value")
        canonical_messages.append({"role": role, "content": content})
        prompt_chars += len(content)

    canonical_prompt = json.dumps(
        canonical_messages,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "prompt_message_count": len(canonical_messages),
        "prompt_chars": prompt_chars,
        "prompt_fingerprint": hashlib.sha256(canonical_prompt).hexdigest()[:16],
    }


def _debug_completion_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) - _DEBUG_ARTIFACT_COMPLETION_FIELDS:
        raise ValueError("unsafe debug artifact completion shape")

    safe: dict[str, object] = {}
    for field_name in ("provider", "model", "finish_reason", "request_id", "outcome"):
        if field_name not in value:
            continue
        field_value = _require_debug_string_or_none(value[field_name], field_name=field_name)
        if field_value is not None:
            safe[field_name] = field_value

    content = value.get("content")
    if content is None:
        return safe
    if not isinstance(content, str):
        raise ValueError("unsafe debug artifact type for completion content")
    safe["response_chars"] = len(content)
    safe["response_fingerprint"] = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return safe


def _debug_completion_error_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) - _DEBUG_ARTIFACT_ERROR_FIELDS:
        raise ValueError("unsafe debug artifact completion error shape")

    safe: dict[str, object] = {}
    code = value.get("code")
    if code is not None:
        if not isinstance(code, str):
            raise ValueError("unsafe debug artifact type for completion error code")
        safe["error_code"] = code

    message = value.get("message")
    if message is not None:
        if not isinstance(message, str):
            raise ValueError("unsafe debug artifact type for completion error message")
        safe["error_message_present"] = True
    return safe


def _debug_parse_metadata(payload: dict[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    if "parse_error" in payload:
        parse_error = payload["parse_error"]
        if not isinstance(parse_error, str):
            raise ValueError("unsafe debug artifact type for parse_error")
        safe["parse_error_present"] = True

    if "parsed" in payload:
        parsed = payload["parsed"]
        safe["parsed_type"] = type(parsed).__name__
        if isinstance(parsed, dict):
            safe["parsed_top_level_key_count"] = len(parsed)
    return safe


def _redact_debug_artifact_payload(payload: dict[str, object]) -> dict[str, object]:
    """Convert the legacy diagnostic structure into metadata-only persisted content."""

    if set(payload) - _DEBUG_ARTIFACT_ALLOWED_FIELDS:
        raise ValueError("unsafe debug artifact top-level field")

    safe: dict[str, object] = {}
    if "attempt" in payload:
        attempt = payload["attempt"]
        if isinstance(attempt, bool) or not isinstance(attempt, int):
            raise ValueError("unsafe debug artifact type for attempt")
        safe["attempt"] = attempt

    if "response_model" in payload:
        response_model = payload["response_model"]
        if not isinstance(response_model, str):
            raise ValueError("unsafe debug artifact type for response_model")
        safe["response_model"] = response_model

    if "messages" in payload:
        safe.update(_debug_prompt_metadata(payload["messages"]))
    if "completion" in payload:
        safe.update(_debug_completion_metadata(payload["completion"]))
    if "completion_error" in payload:
        safe.update(_debug_completion_error_metadata(payload["completion_error"]))
    safe.update(_debug_parse_metadata(payload))
    return safe


def _make_debug_artifact_writer(
    settings: WebSettings,
    session_id: str,
    *,
    stage: str,
) -> Callable[[dict[str, object]], None] | None:
    """Return an opt-in metadata-only writer for redacted LLM diagnostics.

    Raw prompts, user content, provider bodies, authorization headers, and raw error
    strings are never persisted. The writer performs this policy at the storage
    boundary so future callers cannot accidentally restore the former raw-capture
    behavior merely by passing sensitive diagnostic structures.
    """

    if not settings.llm.debug_artifact_capture:
        return None

    artifact_dir = _debug_artifact_dir(settings, session_id)

    def writer(payload: dict[str, object]) -> None:
        try:
            safe_payload = _redact_debug_artifact_payload(payload)
        except ValueError as exc:
            LOGGER.warning(
                "wizard debug artifact rejected unsafe capture payload",
                extra={"stage": stage, "error_type": type(exc).__name__},
            )
            return

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        suffix = uuid4().hex[:8]
        artifact_path = artifact_dir / f"{stage}_{stamp}_{suffix}.json"
        try:
            _ensure_private_directory(artifact_dir)
            atomic_write_json(artifact_path, safe_payload)
            _prune_debug_artifacts(artifact_dir, stage=stage, newest=artifact_path)
        except (OSError, PersistenceError) as exc:
            LOGGER.warning(
                "wizard debug artifact capture failed",
                extra={
                    "artifact_name": artifact_path.name,
                    "stage": stage,
                    "error_type": type(exc).__name__,
                },
            )

    return writer


def read_wizard_session(settings: WebSettings, session_id: str) -> WizardSessionDetail:
    path = _session_json_path(settings, session_id)
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        return WizardSessionDetail.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        LOGGER.error(
            "invalid persisted wizard state",
            extra={
                "session_id": session_id,
                "state_file": path.name,
                "error_type": type(exc).__name__,
            },
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        raise PersistedStateError(
            "Persisted wizard state is unreadable or invalid.",
            details={"session_id": session_id, "filename": path.name},
        ) from exc


def _append_message(
    session: WizardSessionDetail,
    *,
    role: WizardMessageRole,
    content: str,
) -> WizardSessionDetail:
    return session.model_copy(
        update={
            "messages": [*session.messages, WizardMessage(role=role, content=content)],
            "updated_at": _utc_now(),
        }
    )


def _set_error(
    session: WizardSessionDetail,
    error_payload: dict[str, object],
    *,
    failure_kind: WizardFailureKind = "operational",
) -> WizardSessionDetail:
    return session.model_copy(
        update={
            "status": "failed",
            "failure_kind": failure_kind,
            "error": error_payload,
            "updated_at": _utc_now(),
        }
    )
