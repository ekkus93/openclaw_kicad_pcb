"""Trusted wizard/job composition for production schematic refinement."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.errors import UserError

from ..errors import (
    ConflictError,
    PersistedStateError,
    PersistenceError,
    ResourceNotFoundError,
)
from ..settings import WebSettings
from ._wizard_session_io import read_wizard_session
from .artifacts import create_project_zip
from .configured_refinement import run_configured_refinement_request
from .jobs import JobRecord, read_job, update_job_status
from .llm import LlmClient
from .netlists import PreviewGenerationError, _generate_schematic_preview
from .refinement_api import RefinementRunRequest, RefinementRunResponse
from .refinement_config import RefinementFeatureConfig, require_refinement_enabled
from .refinement_runtime import RefinementRuntimeInputs, build_refinement_runtime
from .resource_locks import resource_lock
from .schematic_refinement import RefinementProvenance, RefinementRuntime

LOGGER = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class WizardRefinementTarget:
    """Trusted current generated project selected from persisted wizard/job state."""

    wizard_session_id: str
    job: JobRecord
    accepted_path: Path
    authoritative_ir: CircuitIR
    work_dir: Path
    evidence_root: Path


def run_wizard_refinement_request(
    *,
    settings: WebSettings,
    llm_client: LlmClient | None,
    request: RefinementRunRequest,
    config: RefinementFeatureConfig,
) -> RefinementRunResponse:
    """Refine the current generated project owned by one persisted wizard session."""

    require_refinement_enabled(config)
    client = _require_refinement_llm(settings, llm_client)
    with resource_lock(settings, kind="wizard", resource_id=request.session_id):
        target = resolve_wizard_refinement_target(settings, request.session_id)
        runtime = _build_runtime(settings, client, target)
        response = run_configured_refinement_request(
            accepted_path=target.accepted_path,
            runtime=runtime,
            request=request,
            config=config,
        )
        _refresh_derived_job_artifacts(settings, target, response)
        return response


def resolve_wizard_refinement_target(
    settings: WebSettings,
    session_id: str,
) -> WizardRefinementTarget:
    """Resolve the current generated schematic without trusting request-selected paths."""

    try:
        session = read_wizard_session(settings, session_id)
    except FileNotFoundError as exc:
        raise ResourceNotFoundError(
            "Wizard session not found.",
            details={"session_id": session_id},
        ) from exc

    if (
        session.status != "completed"
        or session.latest_job_id is None
        or session.ir_json is None
        or session.ir_validation is None
        or not session.ir_validation.valid
    ):
        raise ConflictError(
            "Wizard session has no current completed generated project eligible for refinement.",
            code="REFINEMENT_TARGET_NOT_READY",
            details={"session_id": session_id, "status": session.status},
        )

    job = _read_current_job(settings, session.latest_job_id, session_id=session_id)
    _require_job_matches_current_ir(job, session.ir_json, session_id=session_id)
    authoritative_ir = _load_authoritative_ir(job, session.ir_json, session_id=session_id)
    accepted_path = _resolve_schematic_path(job, session_id=session_id)
    refinement_root = _refinement_root(settings, session_id=session_id, job_id=job.id)
    return WizardRefinementTarget(
        wizard_session_id=session_id,
        job=job,
        accepted_path=accepted_path,
        authoritative_ir=authoritative_ir,
        work_dir=refinement_root / "work",
        evidence_root=refinement_root / "evidence",
    )


def _require_refinement_llm(
    settings: WebSettings,
    llm_client: LlmClient | None,
) -> LlmClient:
    if not settings.llm.enabled or llm_client is None or settings.llm.model is None:
        raise UserError(
            "Schematic refinement requires an enabled configured LLM provider and model.",
            code="VISION_CAPABILITY_UNAVAILABLE",
        )
    if not settings.llm.vision_enabled:
        raise UserError(
            "The configured LLM runtime does not enable image input for schematic refinement.",
            code="VISION_CAPABILITY_UNAVAILABLE",
        )
    return llm_client


def _read_current_job(settings: WebSettings, job_id: str, *, session_id: str) -> JobRecord:
    try:
        job = read_job(settings, job_id)
    except FileNotFoundError as exc:
        raise PersistedStateError(
            "Wizard session references a generated job that is no longer available.",
            code="REFINEMENT_TARGET_JOB_MISSING",
            details={"session_id": session_id, "job_id": job_id},
        ) from exc
    if job.status != "succeeded" or job.result is None:
        raise ConflictError(
            "Wizard session does not reference a successful generated project.",
            code="REFINEMENT_TARGET_NOT_READY",
            details={"session_id": session_id, "job_id": job_id, "job_status": job.status},
        )
    return job


def _require_job_matches_current_ir(
    job: JobRecord,
    ir_json: dict[str, Any],
    *,
    session_id: str,
) -> None:
    request_ir = job.request.get("netlist_json")
    if request_ir != ir_json:
        raise ConflictError(
            "Wizard session Circuit IR no longer matches its latest generated project.",
            code="REFINEMENT_TARGET_STALE",
            details={"session_id": session_id, "job_id": job.id},
        )


def _load_authoritative_ir(
    job: JobRecord,
    expected_ir_json: dict[str, Any],
    *,
    session_id: str,
) -> CircuitIR:
    try:
        payload = json.loads(job.input_path.read_text(encoding="utf-8"))
        if payload != expected_ir_json:
            raise ValueError("job input does not match current wizard IR")
        return CircuitIR.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, ValidationError) as exc:
        raise PersistedStateError(
            "Generated job Circuit IR is unreadable or does not match wizard state.",
            code="REFINEMENT_TARGET_IR_INVALID",
            details={"session_id": session_id, "job_id": job.id},
        ) from exc


def _resolve_schematic_path(job: JobRecord, *, session_id: str) -> Path:
    result = job.result or {}
    raw_path = result.get("schematic_path")
    if not isinstance(raw_path, str) or not raw_path:
        raise PersistedStateError(
            "Generated job is missing its canonical schematic reference.",
            code="REFINEMENT_TARGET_SCHEMATIC_MISSING",
            details={"session_id": session_id, "job_id": job.id},
        )

    relative_path = Path(raw_path)
    if relative_path.is_absolute():
        raise PersistedStateError(
            "Generated job contains an unsafe schematic reference.",
            code="REFINEMENT_TARGET_PATH_INVALID",
            details={"session_id": session_id, "job_id": job.id},
        )

    work_root = job.work_dir.resolve()
    candidate = (work_root / relative_path).resolve()
    try:
        candidate.relative_to(work_root)
    except ValueError as exc:
        raise PersistedStateError(
            "Generated job contains an unsafe schematic reference.",
            code="REFINEMENT_TARGET_PATH_INVALID",
            details={"session_id": session_id, "job_id": job.id},
        ) from exc
    if candidate.suffix != ".kicad_sch" or not candidate.is_file():
        raise PersistedStateError(
            "Generated job canonical schematic is missing or invalid.",
            code="REFINEMENT_TARGET_SCHEMATIC_MISSING",
            details={"session_id": session_id, "job_id": job.id},
        )
    return candidate


def _refinement_root(settings: WebSettings, *, session_id: str, job_id: str) -> Path:
    wizard_root = (settings.data_dir / "wizard_sessions").resolve()
    candidate = (wizard_root / session_id / "refinement" / job_id).resolve()
    try:
        candidate.relative_to(wizard_root)
    except ValueError as exc:
        raise PersistedStateError(
            "Refinement workspace escaped the configured wizard data root.",
            code="REFINEMENT_TARGET_PATH_INVALID",
            details={"session_id": session_id, "job_id": job_id},
        ) from exc
    return candidate


def _build_runtime(
    settings: WebSettings,
    llm_client: LlmClient,
    target: WizardRefinementTarget,
) -> RefinementRuntime:
    model = settings.llm.model
    if model is None:
        raise UserError(
            "Schematic refinement requires explicit provider/model provenance.",
            code="REFINEMENT_PROVENANCE_REQUIRED",
        )
    return build_refinement_runtime(
        RefinementRuntimeInputs(
            authoritative_ir=target.authoritative_ir,
            adapter=KicadCliAdapter(),
            llm_client=llm_client,
            work_dir=target.work_dir,
            evidence_root=target.evidence_root,
        ),
        RefinementProvenance(provider=settings.llm.provider, model=model),
    )


def _refresh_derived_job_artifacts(
    settings: WebSettings,
    target: WizardRefinementTarget,
    response: RefinementRunResponse,
) -> None:
    preview_warning = _refresh_preview(target)
    _refresh_project_archive(target)

    result = dict(target.job.result or {})
    result["preview_warning"] = preview_warning
    result["refinement"] = {
        "session_id": response.session_id,
        "status": response.status,
        "stop_reason": response.stop_reason,
        "final_accepted_hash": response.final_accepted_hash,
        "evidence_available": response.evidence_available,
    }
    try:
        update_job_status(
            settings,
            target.job,
            status="succeeded",
            result=result,
            error=target.job.error,
        )
    except Exception as exc:
        raise PersistenceError(
            "Refinement was committed, but generated-job metadata could not be refreshed.",
            code="REFINEMENT_DERIVED_STATE_REFRESH_FAILED",
            details={
                "session_id": target.wizard_session_id,
                "job_id": target.job.id,
                "authoritative_committed": True,
            },
        ) from exc


def _refresh_preview(target: WizardRefinementTarget) -> str | None:
    preview_path = target.job.artifacts_dir / "schematic_preview.png"
    try:
        _generate_schematic_preview(target.accepted_path, target.job.artifacts_dir)
    except PreviewGenerationError as exc:
        try:
            preview_path.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            raise PersistenceError(
                "Refinement was committed, but a stale schematic preview could not be removed.",
                code="REFINEMENT_DERIVED_STATE_REFRESH_FAILED",
                details={
                    "session_id": target.wizard_session_id,
                    "job_id": target.job.id,
                    "authoritative_committed": True,
                },
            ) from cleanup_exc
        LOGGER.warning(
            "refinement schematic preview refresh skipped",
            extra={
                "session_id": target.wizard_session_id,
                "job_id": target.job.id,
                "error_type": type(exc).__name__,
            },
        )
        return str(exc)
    return None


def _refresh_project_archive(target: WizardRefinementTarget) -> None:
    zip_path = target.job.artifacts_dir / "project.zip"
    try:
        create_project_zip(target.job.project_dir, target.job.artifacts_dir)
    except Exception as exc:
        try:
            zip_path.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            raise PersistenceError(
                "Refinement was committed, but stale project archive cleanup also failed.",
                code="REFINEMENT_DERIVED_STATE_REFRESH_FAILED",
                details={
                    "session_id": target.wizard_session_id,
                    "job_id": target.job.id,
                    "authoritative_committed": True,
                },
            ) from cleanup_exc
        raise PersistenceError(
            "Refinement was committed, but the downloadable project archive could not be refreshed.",
            code="REFINEMENT_DERIVED_STATE_REFRESH_FAILED",
            details={
                "session_id": target.wizard_session_id,
                "job_id": target.job.id,
                "authoritative_committed": True,
            },
        ) from exc
