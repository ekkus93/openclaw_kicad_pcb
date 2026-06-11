"""Wizard session file I/O: persistence, reads, state helpers."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from kicad_pcb.errors import UserError

from ..errors import kicad_error_to_payload
from ..settings import WebSettings
from ..wizard_models import WizardMessage, WizardMessageRole, WizardSessionDetail

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


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _persist_session(settings: WebSettings, session: WizardSessionDetail) -> WizardSessionDetail:
    session_dir = _session_dir(settings, session.id)
    session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(_session_json_path(settings, session.id), session.model_dump(mode="json"))
    if session.spec is not None:
        _write_json(session_dir / "spec.json", session.spec.model_dump(mode="json"))
    if session.ir_json is not None:
        _write_json(session_dir / "circuit_ir.json", session.ir_json)
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
        index = len(list(artifact_dir.glob(f"{stage}_*.json"))) + 1
        _write_json(artifact_dir / f"{stage}_{index:02d}.json", payload)

    return writer


def read_wizard_session(settings: WebSettings, session_id: str) -> WizardSessionDetail:
    path = _session_json_path(settings, session_id)
    if not path.is_file():
        raise FileNotFoundError(path)
    return WizardSessionDetail.model_validate_json(path.read_text(encoding="utf-8"))


def update_wizard_session_metadata(
    *,
    settings: WebSettings,
    session_id: str,
    project_name: str | None,
    symbols_dir: str | None,
) -> WizardSessionDetail:
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


def _set_error(session: WizardSessionDetail, exc: Exception) -> WizardSessionDetail:
    if isinstance(exc, UserError):
        error_payload = kicad_error_to_payload(exc)["error"]
    else:
        error_payload = {
            "type": "internal_error",
            "code": "INTERNAL_SERVER_ERROR",
            "message": str(exc),
            "details": {},
        }
    return session.model_copy(
        update={"status": "failed", "error": error_payload, "updated_at": _utc_now()}
    )
