from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb_web.errors import ConflictError, PersistedStateError, PersistenceError
from kicad_pcb_web.services import wizard_refinement as service
from kicad_pcb_web.services._wizard_session_io import _persist_session
from kicad_pcb_web.services.jobs import JobRecord, read_job, write_job
from kicad_pcb_web.services.refinement_api import RefinementRunRequest, RefinementRunResponse
from kicad_pcb_web.services.refinement_config import RefinementFeatureConfig
from kicad_pcb_web.services.schematic_refinement import RefinementRuntime
from kicad_pcb_web.settings import LlmSettings, WebSettings
from kicad_pcb_web.wizard_models import WizardIrValidation, WizardSessionDetail


def _ir_json(*, value: str = "1k") -> dict[str, Any]:
    return {
        "version": "1",
        "components": [
            {
                "ref": "R1",
                "symbol": "Device:R",
                "value": value,
            }
        ],
        "nets": [
            {
                "name": "NET1",
                "pins": [
                    {"ref": "R1", "pin": "1"},
                ],
            }
        ],
    }


def _settings(tmp_path: Path, *, vision_enabled: bool = True) -> WebSettings:
    data_dir = tmp_path / "data"
    return WebSettings(
        data_dir=data_dir,
        jobs_dir=data_dir / "jobs",
        llm=LlmSettings(
            provider="openai",
            model="vision-model",
            vision_enabled=vision_enabled,
        ),
    )


def _seed_completed_project(
    tmp_path: Path,
) -> tuple[WebSettings, WizardSessionDetail, JobRecord, Path]:
    settings = _settings(tmp_path)
    ir_json = _ir_json()
    job_id = "job-001"
    work_dir = settings.jobs_dir / job_id
    project_dir = work_dir / "project" / "Project"
    artifacts_dir = work_dir / "artifacts"
    input_path = work_dir / "input" / "circuit_ir.json"
    schematic_path = project_dir / "Project.kicad_sch"
    project_dir.mkdir(parents=True)
    artifacts_dir.mkdir(parents=True)
    input_path.parent.mkdir(parents=True)
    input_path.write_text(json.dumps(ir_json), encoding="utf-8")
    schematic_path.write_text("(kicad_sch)", encoding="utf-8")

    job = JobRecord(
        id=job_id,
        status="succeeded",
        project_name="Project",
        created_at="2026-08-11T00:00:00Z",
        updated_at="2026-08-11T00:00:00Z",
        work_dir=work_dir,
        input_path=input_path,
        project_dir=project_dir,
        artifacts_dir=artifacts_dir,
        request={"netlist_json": ir_json},
        result={
            "schematic_path": "project/Project/Project.kicad_sch",
            "project_zip": "artifacts/project.zip",
            "preview_warning": None,
        },
    )
    write_job(settings, job)

    session = WizardSessionDetail(
        id="wiz_test",
        status="completed",
        created_at="2026-08-11T00:00:00Z",
        updated_at="2026-08-11T00:00:00Z",
        project_name="Project",
        spec_approved=True,
        ir_json=ir_json,
        ir_validation=WizardIrValidation(valid=True, component_count=1, net_count=1),
        latest_job_id=job_id,
    )
    _persist_session(settings, session)
    return settings, session, read_job(settings, job_id), schematic_path


def _response(session_id: str) -> RefinementRunResponse:
    return RefinementRunResponse(
        status="stopped",
        stop_reason="REFINEMENT_STOP_NO_OPERATIONS",
        session_id=session_id,
        starting_hash="a" * 64,
        final_accepted_hash="b" * 64,
        best_accepted_hash="b" * 64,
        latest_attempted_hash=None,
        starting_layout_fingerprint="c" * 64,
        final_layout_fingerprint="d" * 64,
        rounds_attempted=1,
        accepted_rounds=1,
        rejected_rounds=0,
        accepted_operations=1,
        model_calls_made=2,
        model_call_limit=6,
        evidence_available=True,
    )


def test_resolver_binds_current_wizard_ir_job_and_contained_schematic(tmp_path: Path) -> None:
    settings, session, job, schematic_path = _seed_completed_project(tmp_path)

    target = service.resolve_wizard_refinement_target(settings, session.id)

    assert target.job.id == job.id
    assert target.accepted_path == schematic_path.resolve()
    assert target.authoritative_ir.components[0].ref == "R1"
    assert target.authoritative_ir.components[0].value == "1k"
    assert target.work_dir == (
        settings.data_dir / "wizard_sessions" / session.id / "refinement" / job.id / "work"
    ).resolve()
    assert target.evidence_root == (
        settings.data_dir / "wizard_sessions" / session.id / "refinement" / job.id / "evidence"
    ).resolve()


def test_stale_wizard_ir_cannot_dispatch_refinement(monkeypatch, tmp_path: Path) -> None:
    settings, session, _, schematic_path = _seed_completed_project(tmp_path)
    stale = session.model_copy(update={"ir_json": _ir_json(value="2k")})
    _persist_session(settings, stale)
    calls = 0

    def fake_run(**kwargs):
        nonlocal calls
        calls += 1
        schematic_path.write_text("mutated", encoding="utf-8")
        return _response(stale.id)

    monkeypatch.setattr(service, "run_configured_refinement_request", fake_run)

    with pytest.raises(ConflictError, match="no longer matches") as exc_info:
        service.run_wizard_refinement_request(
            settings=settings,
            llm_client=object(),  # type: ignore[arg-type]
            request=RefinementRunRequest(session_id=stale.id),
            config=RefinementFeatureConfig(enabled=True),
        )

    assert exc_info.value.code == "REFINEMENT_TARGET_STALE"
    assert calls == 0
    assert schematic_path.read_text(encoding="utf-8") == "(kicad_sch)"


def test_unsafe_persisted_schematic_path_is_rejected(tmp_path: Path) -> None:
    settings, session, job, _ = _seed_completed_project(tmp_path)
    result = dict(job.result or {})
    result["schematic_path"] = "../../escape.kicad_sch"
    write_job(settings, replace(job, result=result))

    with pytest.raises(PersistedStateError, match="unsafe schematic reference") as exc_info:
        service.resolve_wizard_refinement_target(settings, session.id)

    assert exc_info.value.code == "REFINEMENT_TARGET_PATH_INVALID"


def test_vision_disabled_refuses_before_target_lookup_or_dispatch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, vision_enabled=False)
    calls = 0

    def fake_run(**kwargs):
        nonlocal calls
        calls += 1
        return _response("wiz_missing")

    monkeypatch.setattr(service, "run_configured_refinement_request", fake_run)

    with pytest.raises(UserError, match="does not enable image input") as exc_info:
        service.run_wizard_refinement_request(
            settings=settings,
            llm_client=object(),  # type: ignore[arg-type]
            request=RefinementRunRequest(session_id="wiz_missing"),
            config=RefinementFeatureConfig(enabled=True),
        )

    assert exc_info.value.code == "VISION_CAPABILITY_UNAVAILABLE"
    assert calls == 0


def test_success_refreshes_project_artifacts_and_job_metadata(monkeypatch, tmp_path: Path) -> None:
    settings, session, job, schematic_path = _seed_completed_project(tmp_path)
    observed: dict[str, object] = {}

    def fake_run(**kwargs):
        observed.update(kwargs)
        return _response(session.id)

    def fake_preview(path: Path, artifacts_dir: Path) -> Path:
        assert path == schematic_path.resolve()
        preview = artifacts_dir / "schematic_preview.png"
        preview.write_bytes(b"preview")
        return preview

    def fake_zip(project_dir: Path, artifacts_dir: Path) -> Path:
        assert project_dir == job.project_dir
        archive = artifacts_dir / "project.zip"
        archive.write_bytes(b"zip")
        return archive

    monkeypatch.setattr(service, "run_configured_refinement_request", fake_run)
    monkeypatch.setattr(service, "_generate_schematic_preview", fake_preview)
    monkeypatch.setattr(service, "create_project_zip", fake_zip)

    response = service.run_wizard_refinement_request(
        settings=settings,
        llm_client=object(),  # type: ignore[arg-type]
        request=RefinementRunRequest(session_id=session.id),
        config=RefinementFeatureConfig(enabled=True),
    )

    assert response.final_accepted_hash == "b" * 64
    assert observed["accepted_path"] == schematic_path.resolve()
    runtime = cast(RefinementRuntime, observed["runtime"])
    assert runtime.authoritative_ir.components[0].ref == "R1"
    assert runtime.provenance is not None
    assert runtime.provenance.provider == "openai"
    assert runtime.provenance.model == "vision-model"

    refreshed = read_job(settings, job.id)
    assert refreshed.result is not None
    assert refreshed.result["refinement"] == {
        "session_id": session.id,
        "status": "stopped",
        "stop_reason": "REFINEMENT_STOP_NO_OPERATIONS",
        "final_accepted_hash": "b" * 64,
        "evidence_available": True,
    }
    assert refreshed.result["preview_warning"] is None
    assert (job.artifacts_dir / "project.zip").read_bytes() == b"zip"
    assert (job.artifacts_dir / "schematic_preview.png").read_bytes() == b"preview"


def test_archive_refresh_failure_removes_stale_zip_and_reports_committed_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings, session, job, _ = _seed_completed_project(tmp_path)
    stale_zip = job.artifacts_dir / "project.zip"
    stale_zip.write_bytes(b"stale")

    monkeypatch.setattr(
        service,
        "run_configured_refinement_request",
        lambda **kwargs: _response(session.id),
    )

    def fake_preview(path: Path, artifacts_dir: Path) -> Path:
        preview = artifacts_dir / "schematic_preview.png"
        preview.write_bytes(b"preview")
        return preview

    monkeypatch.setattr(service, "_generate_schematic_preview", fake_preview)

    def fail_zip(project_dir: Path, artifacts_dir: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(service, "create_project_zip", fail_zip)

    with pytest.raises(PersistenceError, match="downloadable project archive") as exc_info:
        service.run_wizard_refinement_request(
            settings=settings,
            llm_client=object(),  # type: ignore[arg-type]
            request=RefinementRunRequest(session_id=session.id),
            config=RefinementFeatureConfig(enabled=True),
        )

    assert exc_info.value.code == "REFINEMENT_DERIVED_STATE_REFRESH_FAILED"
    assert exc_info.value.details["authoritative_committed"] is True
    assert not stale_zip.exists()
