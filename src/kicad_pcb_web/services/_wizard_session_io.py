"""Wizard session file I/O: persistence, reads, and state helpers."""

from __future__ import annotations

import json
import logging
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


def _persist_session(settings: WebSettings, session: WizardSessionDetail) -> WizardSessionDetail:
    """Commit authoritative wizard state and refresh derived convenience exports.

    ``wizard.json`` is authoritative. ``spec.json`` and ``circuit_ir.json`` are
    derived exports only; ``derived_state.json`` identifies the authoritative
    revision that produced them.
    """

    session_dir = _session_dir(settings, session.id)
    session_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_session_json_path(settings, session.id), session.model_dump(mode="json"))

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
    return session


def _debug_artifact_dir(settings: WebSettings, session_id: str) -> Path:
    return _session_dir(settings, session_id) / "debug_artifacts"


def _make_debug_artifact_writer(
    settings: WebSettings,
    session_id: str,
    *,
    stage: str,
) -> Callable[[dict[str, object]], None] | None:
    if not settings.llm.debug_artifact_capture:
        return None

    artifact_dir = _debug_artifact_dir(settings, session_id)

    def writer(payload: dict[str, object]) -> None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
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


def update_wizard_session_metadata(
    *,
    settings: WebSettings,
    session_id: str,
    project_name: str | None,
    symbols_dir: str | None,
) -> WizardSessionDetail:
    """Compatibility helper; callers should prefer one locked mutation transaction."""

    session = read_wizard_session(settings, session_id)
    updated = session.model_copy(
        update={
            "project_name": project_name,
            "symbols_dir": symbols_dir,
            "updated_at": _utc_now(),
        }
    )
    return _persist_session(settings, updated)


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
