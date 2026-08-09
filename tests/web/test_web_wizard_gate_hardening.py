"""Backend-only wizard gate regressions for approval and project readiness."""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb_web.errors import ConflictError
from kicad_pcb_web.services._wizard_session_io import _persist_session
from kicad_pcb_web.services.wizard import approve_wizard_spec, generate_wizard_project
from kicad_pcb_web.settings import WebSettings
from kicad_pcb_web.wizard_models import (
    CircuitBlockSpec,
    CircuitSpec,
    WizardIrValidation,
    WizardSessionDetail,
)


def _settings(tmp_path: Path) -> WebSettings:
    data_dir = tmp_path / "data"
    return WebSettings(data_dir=data_dir, jobs_dir=data_dir / "jobs")


def _review_session(
    *,
    session_id: str,
    spec: CircuitSpec | None = None,
    open_questions: list[str] | None = None,
    unsupported_reasons: list[str] | None = None,
) -> WizardSessionDetail:
    return WizardSessionDetail(
        id=session_id,
        status="spec_ready_for_review",
        created_at="2026-08-09T00:00:00Z",
        updated_at="2026-08-09T00:00:01Z",
        spec=spec or CircuitSpec(project_name="GateTest", purpose="Exercise backend gates."),
        open_questions=open_questions or [],
        unsupported_reasons=unsupported_reasons or [],
    )


@pytest.mark.parametrize(
    ("session", "expected_code"),
    [
        (
            _review_session(
                session_id="wiz_session_question",
                open_questions=["Which regulator should be used?"],
            ),
            "WIZARD_SPEC_HAS_OPEN_QUESTIONS",
        ),
        (
            _review_session(
                session_id="wiz_spec_question",
                spec=CircuitSpec(
                    project_name="GateTest",
                    purpose="Exercise backend gates.",
                    open_questions=["Which regulator should be used?"],
                ),
            ),
            "WIZARD_SPEC_HAS_OPEN_QUESTIONS",
        ),
        (
            _review_session(
                session_id="wiz_spec_unsupported",
                spec=CircuitSpec(
                    project_name="GateTest",
                    purpose="Exercise backend gates.",
                    unsupported_reasons=["Unsupported requirement."],
                ),
            ),
            "WIZARD_SPEC_UNSUPPORTED",
        ),
        (
            _review_session(
                session_id="wiz_custom_block",
                spec=CircuitSpec(
                    project_name="GateTest",
                    purpose="Exercise backend gates.",
                    blocks=[
                        CircuitBlockSpec(
                            name="Mystery stage",
                            block_type="custom",
                            summary="Needs a concrete implementation.",
                            required_components=[],
                        )
                    ],
                ),
            ),
            "WIZARD_SPEC_UNDERSPECIFIED_CUSTOM_BLOCKS",
        ),
    ],
)
def test_backend_approval_rejects_unresolved_correctness_requirements(
    tmp_path: Path,
    session: WizardSessionDetail,
    expected_code: str,
) -> None:
    settings = _settings(tmp_path)
    _persist_session(settings, session)

    with pytest.raises(ConflictError) as caught:
        approve_wizard_spec(settings=settings, session_id=session.id)

    assert caught.value.code == expected_code


def test_backend_approval_accepts_reviewable_resolved_spec(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    session = _review_session(session_id="wiz_resolved")
    _persist_session(settings, session)

    approved = approve_wizard_spec(settings=settings, session_id=session.id)

    assert approved.status == "spec_approved"
    assert approved.spec_approved is True
    assert approved.spec_approved_at is not None


def test_project_generation_requires_explicit_spec_approval_even_with_valid_ir(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    session = WizardSessionDetail(
        id="wiz_unapproved_valid_ir",
        status="ir_ready_for_generation",
        created_at="2026-08-09T00:00:00Z",
        updated_at="2026-08-09T00:00:01Z",
        spec=CircuitSpec(project_name="GateTest", purpose="Exercise backend gates."),
        spec_approved=False,
        ir_json={"version": "1", "components": [], "nets": []},
        ir_validation=WizardIrValidation(valid=True),
    )
    _persist_session(settings, session)

    with pytest.raises(ConflictError):
        generate_wizard_project(settings=settings, session_id=session.id)
