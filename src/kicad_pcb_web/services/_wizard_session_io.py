"""Wizard session file I/O: persistence, reads, and state helpers."""

from __future__ import annotations

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
from ..wizard_models import WizardMessage, WizardMessageRole, WizardSessionDetail
from .atomic_io import atomic_write_json

LOGGER = logging.getLogger("uvicorn.error")
_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


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


def _make_debug_artifact_writer(
    settings: WebSettings,
    session_id: str,
    *,
    stage: str,
) -> Callable[[dict[str, object]], None] | None:
    """Return an opt-in writer for raw prompts/completions.

    Raw debug artifacts are intentionally separate from request-log redaction.
    Their directory is restricted to the owning user on POSIX systems.
    """

    if not settings.llm.debug_artifact_capture:
        return None

    artifact_dir = _debug_artifact_dir(settings, session_id)

    def writer(payload: dict[str, object]) -> None:
        _ensure_private_directory(artifact_dir)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        suffix = uuid4().hex[:8]
        atomic_write_json(artifact_dir / f"{stage}_{stamp}_{suffix}.json", payload)

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
) -> WizardSessionDetail:
    return session.model_copy(
        update={"status": "failed", "error": error_payload, "updated_at": _utc_now()}
    )
