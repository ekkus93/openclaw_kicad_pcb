"""Wizard orchestration for LLM-assisted circuit design sessions."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, NoReturn, cast

from kicad_pcb.errors import ToolError, UserError

from ..errors import (
    ConflictError,
    ResourceNotFoundError,
    UpstreamProviderError,
    WebServiceError,
    new_error_id,
    web_service_error_to_payload,
)
from ..schemas import CreateJobFromNetlistRequest
from ..settings import WebSettings
from ..wizard_models import (
    CreateWizardSessionRequest,
    IrGenerationOutput,
    SpecConversationOutput,
    WizardGenerateProjectResponse,
    WizardIrValidation,
    WizardMessage,
    WizardMessageRequest,
    WizardSessionDetail,
)
from ._wizard_llm import (
    _build_ir_messages,
    _build_spec_messages,
    _call_llm_for_json,
    _format_ir_repair_error,
    _require_llm_client,
)
from ._wizard_session_io import (
    _append_message,
    _make_debug_artifact_writer,
    _new_session_id,
    _persist_session,
    _set_error,
    _utc_now,
    read_wizard_session,
)
from .llm import LlmClient
from .netlists import generate_project_from_netlist_job, prepare_netlist_dict
from .resource_locks import resource_lock

LOGGER = logging.getLogger("uvicorn.error")


def _read_session_for_mutation(settings: WebSettings, session_id: str) -> WizardSessionDetail:
    try:
        return read_wizard_session(settings, session_id)
    except FileNotFoundError as exc:
        raise ResourceNotFoundError(
            "Wizard session not found.", details={"session_id": session_id}
        ) from exc


def _operation_error(exc: Exception, *, session_id: str, operation: str) -> WebServiceError:
    """Convert an operation failure into a safe typed API error."""

    if isinstance(exc, WebServiceError):
        exc.details.setdefault("session_id", session_id)
        exc.details.setdefault("operation", operation)
        return exc
    if isinstance(exc, ToolError):
        return UpstreamProviderError(
            "The configured LLM provider request failed.",
            details={
                "session_id": session_id,
                "operation": operation,
                "provider_error_code": exc.code,
            },
        )
    if isinstance(exc, UserError):
        return UpstreamProviderError(
            "The configured LLM provider returned an unusable response.",
            details={
                "session_id": session_id,
                "operation": operation,
                "provider_error_code": exc.code,
            },
        )

    error_id = new_error_id()
    LOGGER.error(
        "unexpected wizard operation failure error_id=%s",
        error_id,
        extra={
            "session_id": session_id,
            "operation": operation,
            "error_id": error_id,
            "error_type": type(exc).__name__,
        },
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return WebServiceError(
        "An unexpected internal error occurred.",
        code="INTERNAL_SERVER_ERROR",
        status_code=500,
        details={"session_id": session_id, "operation": operation},
        error_id=error_id,
    )


def _public_error_payload(error: WebServiceError) -> dict[str, object]:
    return cast(dict[str, object], web_service_error_to_payload(error)["error"])


def _failure_operation(session: WizardSessionDetail) -> str | None:
    """Return the operation that placed a session in ``failed`` state, if known."""

    if session.status != "failed" or not isinstance(session.error, dict):
        return None
    details = session.error.get("details")
    if not isinstance(details, dict):
        return None
    operation = details.get("operation")
    return operation if isinstance(operation, str) else None


def _persist_and_raise_failure(
    settings: WebSettings,
    session: WizardSessionDetail,
    exc: Exception,
    *,
    operation: str,
) -> NoReturn:
    error = _operation_error(exc, session_id=session.id, operation=operation)
    if not isinstance(exc, WebServiceError):
        LOGGER.warning(
            "wizard operation failed",
            extra={
                "session_id": session.id,
                "operation": operation,
                "error_code": error.code,
                "error_type": type(exc).__name__,
            },
        )
    failed = _set_error(session, _public_error_payload(error))
    _persist_session(settings, failed)
    raise error from exc


def create_wizard_session(
    *,
    settings: WebSettings,
    request: CreateWizardSessionRequest,
    llm_client: LlmClient | None,
) -> WizardSessionDetail:
    client = _require_llm_client(llm_client)
    now = _utc_now()
    session = WizardSessionDetail(
        id=_new_session_id(),
        status="drafting_spec",
        created_at=now,
        updated_at=now,
        project_name=request.project_name,
        symbols_dir=request.symbols_dir,
        llm_provider=settings.llm.provider,
        prompt_version=settings.llm.system_prompt_version,
        messages=[WizardMessage(role="user", content=request.message)],
    )
    started_at = time.perf_counter()
    with resource_lock(settings, kind="wizard", resource_id=session.id):
        _persist_session(settings, session)
        LOGGER.info(
            "wizard spec draft started",
            extra={
                "session_id": session.id,
                "provider": settings.llm.provider,
                "prompt_version": settings.llm.system_prompt_version,
                "message_count": len(session.messages),
            },
        )
        try:
            output = _call_llm_for_json(
                llm_client=client,
                messages=_build_spec_messages(settings, session),
                response_model=SpecConversationOutput,
                max_repairs=settings.llm.spec_max_repair_rounds,
                debug_artifact_writer=_make_debug_artifact_writer(
                    settings, session.id, stage="spec"
                ),
            )
            session = _append_message(session, role="assistant", content=output.assistant_message)
            session = session.model_copy(
                update={
                    "status": output.next_state,
                    "spec": output.spec,
                    "assumptions": output.assumptions,
                    "open_questions": output.open_questions,
                    "unsupported_reasons": output.unsupported_reasons,
                    "error": None,
                    "updated_at": _utc_now(),
                }
            )
            LOGGER.info(
                "wizard session created",
                extra={
                    "session_id": session.id,
                    "status": session.status,
                    "provider": session.llm_provider,
                    "prompt_version": session.prompt_version,
                    "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
                },
            )
            return _persist_session(settings, session)
        except Exception as exc:
            _persist_and_raise_failure(settings, session, exc, operation="create_spec")


def post_wizard_message(
    *,
    settings: WebSettings,
    session_id: str,
    request: WizardMessageRequest,
    llm_client: LlmClient | None,
) -> WizardSessionDetail:
    client = _require_llm_client(llm_client)
    with resource_lock(settings, kind="wizard", resource_id=session_id):
        session = _read_session_for_mutation(settings, session_id)

        # Preserve the last known-good spec/approval/IR/job checkpoint while the
        # replacement spec is pending. The transient status makes that checkpoint
        # non-actionable. Only a successful replacement invalidates it.
        session = session.model_copy(
            update={
                "project_name": request.project_name,
                "symbols_dir": request.symbols_dir,
                "status": "drafting_spec",
                "error": None,
                "updated_at": _utc_now(),
            }
        )
        session = _append_message(session, role="user", content=request.message)
        _persist_session(settings, session)
        started_at = time.perf_counter()
        LOGGER.info(
            "wizard spec revision started",
            extra={
                "session_id": session.id,
                "provider": settings.llm.provider,
                "message_count": len(session.messages),
            },
        )
        try:
            output = _call_llm_for_json(
                llm_client=client,
                messages=_build_spec_messages(settings, session),
                response_model=SpecConversationOutput,
                max_repairs=settings.llm.spec_max_repair_rounds,
                debug_artifact_writer=_make_debug_artifact_writer(
                    settings, session.id, stage="spec"
                ),
            )
            session = _append_message(session, role="assistant", content=output.assistant_message)
            session = session.model_copy(
                update={
                    "status": output.next_state,
                    "spec": output.spec,
                    "spec_approved": False,
                    "spec_approved_at": None,
                    "ir_json": None,
                    "ir_validation": None,
                    "latest_job_id": None,
                    "assumptions": output.assumptions,
                    "open_questions": output.open_questions,
                    "unsupported_reasons": output.unsupported_reasons,
                    "error": None,
                    "updated_at": _utc_now(),
                }
            )
            LOGGER.info(
                "wizard session updated",
                extra={
                    "session_id": session.id,
                    "status": session.status,
                    "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
                },
            )
            return _persist_session(settings, session)
        except Exception as exc:
            _persist_and_raise_failure(settings, session, exc, operation="revise_spec")


def approve_wizard_spec(*, settings: WebSettings, session_id: str) -> WizardSessionDetail:
    with resource_lock(settings, kind="wizard", resource_id=session_id):
        session = _read_session_for_mutation(settings, session_id)
        if session.spec is None:
            raise ConflictError(
                "Cannot approve a wizard session before a spec draft exists.",
                details={"session_id": session_id},
            )
        if session.unsupported_reasons:
            raise ConflictError(
                "Cannot approve a spec that the wizard marked as unsupported.",
                details={"session_id": session_id},
            )
        if session.status != "spec_ready_for_review":
            raise ConflictError(
                "Only the current reviewable spec can be approved.",
                details={"session_id": session_id, "status": session.status},
            )
        approved_at = _utc_now()
        session = session.model_copy(
            update={
                "status": "spec_approved",
                "spec_approved": True,
                "spec_approved_at": approved_at,
                "updated_at": approved_at,
                "error": None,
            }
        )
        LOGGER.info("wizard spec approved", extra={"session_id": session.id})
        return _persist_session(settings, session)


def clear_wizard_ir(*, settings: WebSettings, session_id: str) -> WizardSessionDetail:
    with resource_lock(settings, kind="wizard", resource_id=session_id):
        session = _read_session_for_mutation(settings, session_id)
        if not session.spec_approved:
            raise ConflictError(
                "Cannot clear Circuit IR on a session whose spec is not approved.",
                details={"session_id": session_id},
            )
        session = session.model_copy(
            update={
                "status": "spec_approved",
                "ir_json": None,
                "ir_validation": None,
                "latest_job_id": None,
                "error": None,
                "updated_at": _utc_now(),
            }
        )
        LOGGER.info("wizard circuit IR cleared", extra={"session_id": session.id})
        return _persist_session(settings, session)


def generate_wizard_ir(
    *,
    settings: WebSettings,
    session_id: str,
    llm_client: LlmClient | None,
) -> WizardSessionDetail:
    client = _require_llm_client(llm_client)
    with resource_lock(settings, kind="wizard", resource_id=session_id):
        session = _read_session_for_mutation(settings, session_id)
        retry_failed_ir = (
            session.status == "failed" and _failure_operation(session) == "generate_ir"
        )
        allowed_status = session.status in {
            "spec_approved",
            "ir_needs_repair",
            "ir_ready_for_generation",
            "completed",
        }
        if session.spec is None or not session.spec_approved:
            raise ConflictError(
                "Approve the circuit spec before generating Circuit IR.",
                details={"session_id": session_id},
            )
        if not allowed_status and not retry_failed_ir:
            raise ConflictError(
                "Circuit IR generation is not allowed from the current wizard state.",
                details={"session_id": session_id, "status": session.status},
            )

        # Keep the prior IR and job checkpoint while regeneration is pending so
        # a transient provider failure does not destroy known-good evidence.
        # The drafting/failed status prevents that preserved checkpoint from
        # being treated as current generation input.
        session = session.model_copy(
            update={
                "status": "drafting_ir",
                "error": None,
                "updated_at": _utc_now(),
            }
        )
        _persist_session(settings, session)
        last_error: str | None = None
        prior_ir_json: dict[str, object] | None = None
        try:
            for _ in range(settings.llm.ir_max_repair_rounds + 1):
                output = _call_llm_for_json(
                    llm_client=client,
                    messages=_build_ir_messages(
                        settings,
                        session,
                        prior_ir_json=prior_ir_json,
                        repair_error=last_error,
                    ),
                    response_model=IrGenerationOutput,
                    max_repairs=0,
                    debug_artifact_writer=_make_debug_artifact_writer(
                        settings, session.id, stage="ir"
                    ),
                )
                try:
                    prepared = prepare_netlist_dict(
                        netlist_json=output.netlist_json,
                        symbols_dir=(
                            Path(session.symbols_dir).expanduser().resolve()
                            if session.symbols_dir
                            else None
                        ),
                        auto_fix=True,
                    )
                    session = _append_message(
                        session, role="assistant", content=output.assistant_message
                    )
                    session = session.model_copy(
                        update={
                            "status": "ir_ready_for_generation",
                            "ir_json": prepared.netlist_json,
                            "ir_validation": WizardIrValidation(
                                valid=True,
                                auto_fixed=prepared.auto_fixed,
                                component_count=prepared.component_count,
                                net_count=prepared.net_count,
                                warnings=prepared.warnings,
                                fixes_applied=prepared.fixes_applied,
                                symbols_dirs_used=prepared.symbols_dirs_used,
                            ),
                            "latest_job_id": None,
                            "assumptions": sorted(
                                {*(session.assumptions or []), *(output.assumptions or [])}
                            ),
                            "error": None,
                            "updated_at": _utc_now(),
                        }
                    )
                    LOGGER.info(
                        "wizard ir ready",
                        extra={
                            "session_id": session.id,
                            "component_count": prepared.component_count,
                            "net_count": prepared.net_count,
                            "auto_fixed": prepared.auto_fixed,
                        },
                    )
                    return _persist_session(settings, session)
                except UserError as exc:
                    last_error = _format_ir_repair_error(exc)
                    prior_ir_json = output.netlist_json

            session = session.model_copy(
                update={
                    "status": "ir_needs_repair",
                    "ir_json": prior_ir_json,
                    "ir_validation": WizardIrValidation(valid=False, error_message=last_error),
                    "latest_job_id": None,
                    "error": None,
                    "updated_at": _utc_now(),
                }
            )
            return _persist_session(settings, session)
        except Exception as exc:
            _persist_and_raise_failure(settings, session, exc, operation="generate_ir")


def _project_failure_error(
    job_payload: dict[str, Any], *, session_id: str, job_id: str
) -> WebServiceError:
    error = job_payload.get("error")
    error_type = error.get("type") if isinstance(error, dict) else None
    status_code = 500
    if error_type == "tool_error":
        status_code = 503
    elif error_type in {"user_error", "validation_error"}:
        status_code = 422
    return WebServiceError(
        "KiCad project generation failed.",
        code="WIZARD_PROJECT_GENERATION_FAILED",
        status_code=status_code,
        details={"session_id": session_id, "job_id": job_id, "operation": "generate_project"},
    )


def generate_wizard_project(
    *,
    settings: WebSettings,
    session_id: str,
) -> WizardGenerateProjectResponse:
    with resource_lock(settings, kind="wizard", resource_id=session_id):
        session = _read_session_for_mutation(settings, session_id)
        retry_failed_project = (
            session.status == "failed" and _failure_operation(session) == "generate_project"
        )
        active_status = session.status in {"ir_ready_for_generation", "completed"}
        if (
            (not active_status and not retry_failed_project)
            or session.ir_json is None
            or session.ir_validation is None
            or not session.ir_validation.valid
        ):
            raise ConflictError(
                "Generate current valid Circuit IR before starting project generation.",
                details={"session_id": session_id, "status": session.status},
            )

        session = session.model_copy(
            update={"status": "generation_started", "updated_at": _utc_now(), "error": None}
        )
        session = _persist_session(settings, session)

        project_name = (
            session.project_name
            or (session.spec.project_name if session.spec is not None else None)
            or "WizardProject"
        )
        netlist_json = session.ir_json
        assert netlist_json is not None
        try:
            job = generate_project_from_netlist_job(
                settings=settings,
                request=CreateJobFromNetlistRequest(
                    project_name=project_name,
                    netlist_json=netlist_json,
                    symbols_dir=session.symbols_dir,
                    validation="internal",
                    auto_fix=False,
                ),
            )
        except Exception as exc:
            _persist_and_raise_failure(settings, session, exc, operation="generate_project")

        if job.status != "succeeded":
            project_error = _project_failure_error(
                job.model_dump(mode="json"), session_id=session.id, job_id=job.id
            )
            session = session.model_copy(
                update={
                    "status": "failed",
                    "latest_job_id": job.id,
                    "error": _public_error_payload(project_error),
                    "updated_at": _utc_now(),
                }
            )
            LOGGER.info(
                "wizard project generation finished",
                extra={"session_id": session.id, "job_id": job.id, "job_status": job.status},
            )
            _persist_session(settings, session)
            raise project_error

        session = session.model_copy(
            update={
                "status": "completed",
                "latest_job_id": job.id,
                "error": None,
                "updated_at": _utc_now(),
            }
        )
        LOGGER.info(
            "wizard project generation finished",
            extra={"session_id": session.id, "job_id": job.id, "job_status": job.status},
        )
        session = _persist_session(settings, session)
        return WizardGenerateProjectResponse(session=session, job=job.model_dump(mode="json"))