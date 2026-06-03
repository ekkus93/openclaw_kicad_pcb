"""Tests for clear_wizard_ir in kicad_pcb_web.services.wizard."""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb_web.services.wizard import (
    _persist_session,
    clear_wizard_ir,
    read_wizard_session,
)
from kicad_pcb_web.settings import LlmSettings, WebSettings
from kicad_pcb_web.wizard_models import (
    CircuitSpec,
    WizardIrValidation,
    WizardMessage,
    WizardSessionDetail,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> WebSettings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return WebSettings(
        data_dir=data_dir,
        jobs_dir=data_dir / "jobs",
        llm=LlmSettings(provider="disabled"),
    )


def _minimal_spec() -> CircuitSpec:
    return CircuitSpec(purpose="test circuit")


def _approved_session(
    settings: WebSettings, *, session_id: str = "wiz_test_abc12345"
) -> WizardSessionDetail:
    """Persist and return a session that has an approved spec and IR."""
    session = WizardSessionDetail(
        id=session_id,
        status="ir_ready_for_generation",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        project_name="TestProject",
        messages=[WizardMessage(role="user", content="build something")],
        spec=_minimal_spec(),
        spec_approved=True,
        spec_approved_at="2026-01-01T00:01:00Z",
        ir_json={"version": "1", "components": [], "nets": []},
        ir_validation=WizardIrValidation(
            valid=True,
            auto_fixed=False,
            component_count=2,
            net_count=3,
            warnings=[],
            fixes_applied=[],
            symbols_dirs_used=[],
        ),
        latest_job_id="job_123",
        error=None,
    )
    return _persist_session(settings, session)


# ---------------------------------------------------------------------------
# 3.1 Happy path — fields cleared correctly
# ---------------------------------------------------------------------------


def test_clear_ir_status_becomes_spec_approved(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    _approved_session(settings)
    result = clear_wizard_ir(settings=settings, session_id="wiz_test_abc12345")
    assert result.status == "spec_approved"


def test_clear_ir_removes_ir_json(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    _approved_session(settings)
    result = clear_wizard_ir(settings=settings, session_id="wiz_test_abc12345")
    assert result.ir_json is None


def test_clear_ir_removes_ir_validation(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    _approved_session(settings)
    result = clear_wizard_ir(settings=settings, session_id="wiz_test_abc12345")
    assert result.ir_validation is None


def test_clear_ir_removes_latest_job_id(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    _approved_session(settings)
    result = clear_wizard_ir(settings=settings, session_id="wiz_test_abc12345")
    assert result.latest_job_id is None


def test_clear_ir_removes_error(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    session = _approved_session(settings)
    # Inject an error into the session
    errored = session.model_copy(update={"error": {"message": "old error"}})
    _persist_session(settings, errored)
    result = clear_wizard_ir(settings=settings, session_id="wiz_test_abc12345")
    assert result.error is None


def test_clear_ir_updated_at_advances(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    before = _approved_session(settings)
    result = clear_wizard_ir(settings=settings, session_id="wiz_test_abc12345")
    assert result.updated_at >= before.updated_at


# ---------------------------------------------------------------------------
# 3.2 Preserved fields
# ---------------------------------------------------------------------------


def test_clear_ir_preserves_spec(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    _approved_session(settings)
    result = clear_wizard_ir(settings=settings, session_id="wiz_test_abc12345")
    assert result.spec is not None
    assert result.spec.purpose == "test circuit"


def test_clear_ir_preserves_messages(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    _approved_session(settings)
    result = clear_wizard_ir(settings=settings, session_id="wiz_test_abc12345")
    assert len(result.messages) == 1
    assert result.messages[0].content == "build something"


def test_clear_ir_preserves_project_name(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    _approved_session(settings)
    result = clear_wizard_ir(settings=settings, session_id="wiz_test_abc12345")
    assert result.project_name == "TestProject"


def test_clear_ir_preserves_spec_approved(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    _approved_session(settings)
    result = clear_wizard_ir(settings=settings, session_id="wiz_test_abc12345")
    assert result.spec_approved is True


def test_clear_ir_preserves_spec_approved_at(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    _approved_session(settings)
    result = clear_wizard_ir(settings=settings, session_id="wiz_test_abc12345")
    assert result.spec_approved_at == "2026-01-01T00:01:00Z"


# ---------------------------------------------------------------------------
# 3.3 Persistence
# ---------------------------------------------------------------------------


def test_clear_ir_persists_to_disk(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    _approved_session(settings)
    clear_wizard_ir(settings=settings, session_id="wiz_test_abc12345")
    on_disk = read_wizard_session(settings, "wiz_test_abc12345")
    assert on_disk.status == "spec_approved"
    assert on_disk.ir_json is None
    assert on_disk.latest_job_id is None


# ---------------------------------------------------------------------------
# 3.4 Precondition guards
# ---------------------------------------------------------------------------


def test_clear_ir_raises_when_spec_not_approved(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    session = WizardSessionDetail(
        id="wiz_test_abc12345",
        status="spec_ready_for_review",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        spec=_minimal_spec(),
        spec_approved=False,
    )
    _persist_session(settings, session)
    with pytest.raises(UserError):
        clear_wizard_ir(settings=settings, session_id="wiz_test_abc12345")


def test_clear_ir_raises_for_missing_session(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    with pytest.raises(FileNotFoundError):
        clear_wizard_ir(settings=settings, session_id="wiz_nonexistent_abc12345")


# ---------------------------------------------------------------------------
# 3.5 Idempotency
# ---------------------------------------------------------------------------


def test_clear_ir_idempotent_when_already_cleared(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    session = WizardSessionDetail(
        id="wiz_test_abc12345",
        status="spec_approved",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        spec=_minimal_spec(),
        spec_approved=True,
        ir_json=None,
        ir_validation=None,
        latest_job_id=None,
    )
    _persist_session(settings, session)
    result = clear_wizard_ir(settings=settings, session_id="wiz_test_abc12345")
    assert result.status == "spec_approved"
    assert result.ir_json is None
