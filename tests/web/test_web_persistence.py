"""Durable persistence, corruption visibility, and mutation-lock tests."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from kicad_pcb_web.main import app
from kicad_pcb_web.errors import (
    PersistedStateError,
    PersistenceError,
    ResourceBusyError,
)
from kicad_pcb_web.services._wizard_session_io import (
    _persist_session,
    read_wizard_session,
)
from kicad_pcb_web.services.atomic_io import atomic_write_json
from kicad_pcb_web.services.jobs import list_jobs
from kicad_pcb_web.services.resource_locks import resource_lock
from kicad_pcb_web.settings import LlmSettings, WebSettings
from kicad_pcb_web.wizard_models import CircuitSpec, WizardIrValidation, WizardSessionDetail


def _settings(tmp_path: Path, *, lock_timeout: float = 0.05) -> WebSettings:
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return WebSettings(
        data_dir=data_dir,
        jobs_dir=jobs_dir,
        mutation_lock_timeout_s=lock_timeout,
        llm=LlmSettings(provider="disabled"),
    )


def _session() -> WizardSessionDetail:
    return WizardSessionDetail(
        id="wiz_test_1234",
        status="ir_ready_for_generation",
        created_at="2026-07-23T00:00:00Z",
        updated_at="2026-07-23T00:01:00Z",
        spec=CircuitSpec(purpose="test"),
        spec_approved=True,
        ir_json={"version": "1", "components": [], "nets": []},
        ir_validation=WizardIrValidation(valid=True),
    )


def test_atomic_write_serialization_failure_preserves_old_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"old": true}\n', encoding="utf-8")

    with pytest.raises(PersistenceError, match="serialize"):
        atomic_write_json(path, {"bad": object()})

    assert json.loads(path.read_text(encoding="utf-8")) == {"old": True}
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_atomic_write_replace_failure_preserves_old_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"old": true}\n', encoding="utf-8")

    with (
        patch("pathlib.Path.replace", side_effect=OSError("nope")),
        pytest.raises(PersistenceError, match="commit"),
    ):
        atomic_write_json(path, {"new": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"old": True}
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_concurrent_atomic_writes_never_produce_truncated_json(tmp_path: Path) -> None:
    path = tmp_path / "state.json"

    def write(index: int) -> None:
        atomic_write_json(path, {"index": index, "payload": "x" * 2000})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(64)))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["index"] in range(64)
    assert payload["payload"] == "x" * 2000
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_persist_session_removes_stale_ir_sidecar(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    session = _persist_session(settings, _session())
    session_dir = settings.data_dir / "wizard_sessions" / session.id
    assert (session_dir / "circuit_ir.json").is_file()

    cleared = session.model_copy(
        update={
            "status": "spec_approved",
            "ir_json": None,
            "ir_validation": None,
            "updated_at": "2026-07-23T00:02:00Z",
        }
    )
    _persist_session(settings, cleared)

    assert not (session_dir / "circuit_ir.json").exists()
    derived = json.loads((session_dir / "derived_state.json").read_text(encoding="utf-8"))
    assert derived["authoritative_file"] == "wizard.json"
    assert derived["ir_present"] is False
    assert read_wizard_session(settings, session.id).ir_json is None


def test_malformed_wizard_state_is_not_reset_or_defaulted(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    path = settings.data_dir / "wizard_sessions" / "wiz_bad" / "wizard.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(PersistedStateError):
        read_wizard_session(settings, "wiz_bad")

    assert path.read_text(encoding="utf-8") == "{not json"


def test_malformed_job_state_is_not_silently_omitted(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    path = settings.jobs_dir / "job_bad" / "job.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(PersistedStateError):
        list_jobs(settings)

    assert path.read_text(encoding="utf-8") == "{}"


def test_missing_canonical_job_state_is_not_silently_omitted(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    (settings.jobs_dir / "job_missing").mkdir()

    with pytest.raises(PersistedStateError, match="missing"):
        list_jobs(settings)


def test_lock_contention_fails_explicitly_and_is_bounded(tmp_path: Path) -> None:
    settings = _settings(tmp_path, lock_timeout=0.01)
    with (
        resource_lock(settings, kind="wizard", resource_id="wiz_test"),
        pytest.raises(ResourceBusyError) as exc_info,
        resource_lock(settings, kind="wizard", resource_id="wiz_test"),
    ):
        raise AssertionError("contended mutation must not run")

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "RESOURCE_BUSY"


def test_jobs_endpoint_surfaces_malformed_state(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    path = data_dir / "jobs" / "job_bad" / "job.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(data_dir))

    response = TestClient(app).get("/api/jobs")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "PERSISTED_STATE_INVALID"
    assert path.read_text(encoding="utf-8") == "{}"


def test_wizard_endpoint_surfaces_malformed_state(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    path = data_dir / "wizard_sessions" / "wiz_bad" / "wizard.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(data_dir))

    response = TestClient(app).get("/api/wizard/sessions/wiz_bad")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "PERSISTED_STATE_INVALID"
    assert path.read_text(encoding="utf-8") == "{not json"


def test_wizard_route_returns_409_while_session_is_locked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path, lock_timeout=0.01)
    session = _session().model_copy(update={"status": "spec_approved"})
    _persist_session(settings, session)
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("KICAD_PCB_WEB_MUTATION_LOCK_TIMEOUT_S", "0.01")

    with resource_lock(settings, kind="wizard", resource_id=session.id):
        response = TestClient(app).post(f"/api/wizard/sessions/{session.id}/clear-ir")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RESOURCE_BUSY"
