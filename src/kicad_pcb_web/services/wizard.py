"""Wizard orchestration for LLM-assisted circuit design sessions."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, NoReturn, cast
from urllib.parse import urlparse

from kicad_pcb.errors import ToolError, UserError

from ..errors import (
    ConflictError,
    PersistenceError,
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
    WizardFailureKind,
    WizardGenerateProjectResponse,
    WizardIrValidation,
    WizardLlmProvenance,
    WizardMessage,
    WizardMessageRequest,
    WizardSessionDetail,
)
from ._wizard_llm import (
    StructuredJsonCallOptions,
    _build_ir_messages,
    _build_spec_messages,
    _call_llm_for_json,
    _call_llm_for_json_once,
    _format_ir_repair_error,
    _raise_structured_output_exhausted,
    _RepairableStructuredOutputError,
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
        retryable = exc.details.get("retryable")
        error = UpstreamProviderError(
            "The configured LLM provider request failed.",
            details={
                "session_id": session_id,
                "operation": operation,
                "provider_error_code": exc.code,
            },
        )
        error.retryable = retryable if isinstance(retryable, bool) else False
        return error
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


def _effective_failure_kind(session: WizardSessionDetail) -> WizardFailureKind | None:
    """Return explicit failure kind, with the documented legacy read fallback."""

    if session.status != "failed":
        return None
    if session.failure_kind is not None:
        return session.failure_kind
    return "unsupported_design" if session.error is None else "operational"


def _failure_operation(session: WizardSessionDetail) -> str | None:
    """Return the operation that placed an operational/generation failure."""

    if _effective_failure_kind(session) == "unsupported_design":
        return None
    if not isinstance(session.error, dict):
        return None
    details = session.error.get("details")
    if not isinstance(details, dict):
        return None
    operation = details.get("operation")
    return operation if isinstance(operation, str) else None


def _endpoint_fingerprint(settings: WebSettings) -> str | None:
    raw = settings.llm.base_url
    if raw is None and settings.llm.provider == "openai":
        raw = "https://api.openai.com/v1"
    if raw is None:
        return None
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    identity = f"{parsed.scheme}://{host}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _llm_provenance(settings: WebSettings) -> WizardLlmProvenance:
    """Return a non-secret reproducibility identity for the current LLM configuration."""

    llm = settings.llm
    endpoint_identity = _endpoint_fingerprint(settings)
    config_payload = {
        "provider": llm.provider,
        "model": llm.model,
        "endpoint_identity": endpoint_identity,
        "timeout_s": llm.timeout_s,
        "temperature": llm.temperature,
        "temperature_mode": llm.temperature_mode,
        "max_tokens": llm.max_tokens,
        "prompt_version": llm.system_prompt_version,
        "spec_max_repair_rounds": llm.spec_max_repair_rounds,
        "ir_max_repair_rounds": llm.ir_max_repair_rounds,
        "retry_max_attempts": llm.retry_max_attempts,
        "retry_base_delay_s": llm.retry_base_delay_s,
        "retry_max_delay_s": llm.retry_max_delay_s,
        "retry_jitter_s": llm.retry_jitter_s,
    }
    canonical = json.dumps(config_payload, sort_keys=True, separators=(",", ":"))
    return WizardLlmProvenance(
        provider=llm.provider,
        model=llm.model,
        prompt_version=llm.system_prompt_version,
        endpoint_identity=endpoint_identity,
        temperature_mode=llm.temperature_mode,
        config_revision=hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16],
    )


def _assert_llm_provenance_matches(
    settings: WebSettings,
    session: WizardSessionDetail,
    *,
    operation: str,
    revision_provenance: WizardLlmProvenance | None,
) -> None:
    """Fail closed before LLM-backed continuation under changed provenance."""

    current = _llm_provenance(settings)
    mismatches: list[str] = []

    if revision_provenance is None:
        mismatches.append("revision_provenance")
    else:
        if revision_provenance.provider != current.provider:
            mismatches.append("provider")
        if revision_provenance.model != current.model:
            mismatches.append("model")
        if revision_provenance.prompt_version != current.prompt_version:
            mismatches.append("prompt_version")
        if revision_provenance.endpoint_identity != current.endpoint_identity:
            mismatches.append("endpoint_identity")
        if revision_provenance.config_revision != current.config_revision:
            mismatches.append("config_revision")

    if mismatches:
        raise ConflictError(
            "Wizard session LLM provenance does not match the current runtime configuration.",
            code="WIZARD_LLM_PROVENANCE_MISMATCH",
            details={
                "session_id": session.id,
                "operation": operation,
                "mismatch_fields": mismatches,
            },
        )


def _persist_and_raise_failure(
    settings: WebSettings,
    session: WizardSessionDetail,
    exc: Exception,
    *,
    operation: str,
    failure_kind: WizardFailureKind = "operational",
) -> NoReturn:
    if isinstance(exc, PersistenceError) and exc.details.get("authoritative_committed") is True:
        raise exc
    error = _operation_error(exc, session_id=session.id, operation=operation)
    if not isinstance(exc, WebServiceError):
        LOGGER.warning(
            "wizard operation failed",
            extra={
                "session_id": session.id,
                "operation": operation,
                "error_code": error.code,
                "error_type": type(exc).__name__,
                "failure_kind": failure_kind,
                "retryable": error.retryable,
            },
        )
    failed = _set_error(session, _public_error_payload(error), failure_kind=failure_kind)
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
        llm_model=settings.llm.model,
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
                "model": settings.llm.model,
                "prompt_version": settings.llm.system_prompt_version,
                "message_count": len(session.messages),
            },
        )
        try:
            output = _call_llm_for_json(
                llm_client=client,
                messages=_build_spec_messages(settings, session),
                response_model=SpecConversationOutput,
                options=StructuredJsonCallOptions(
                    max_repairs=settings.llm.spec_max_repair_rounds,
                    debug_artifact_writer=_make_debug_artifact_writer(
                        settings, session.id, stage="spec"
                    ),
                ),
            )
            session = _append_message(session, role="assistant", content=output.assistant_message)
            session = session.model_copy(
                update={
                    "status": output.next_state,
                    "spec": output.spec,
                    "spec_provenance": _llm_provenance(settings),
                    "assumptions": output.assumptions,
                    "open_questions": output.open_questions,
                    "unsupported_reasons": output.unsupported_reasons,
                    "failure_kind": (
                        "unsupported_design" if output.next_state == "failed" else None
                    ),
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
                    "model": session.llm_model,
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
    with resource_lock(settings, kind="wizard", resource_id=session_id):
        session = _read_session_for_mutation(settings, session_id)
        _assert_llm_provenance_matches(
            settings,
            session,
            operation="revise_spec",
            revision_provenance=session.spec_provenance,
        )
        client = _require_llm_client(llm_client)
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
                options=StructuredJsonCallOptions(
                    max_repairs=settings.llm.spec_max_repair_rounds,
                    debug_artifact_writer=_make_debug_artifact_writer(
                        settings, session.id, stage="spec"
                    ),
                ),
            )
            session = _append_message(session, role="assistant", content=output.assistant_message)
            session = session.model_copy(
                update={
                    "status": output.next_state,
                    "spec": output.spec,
                    "spec_provenance": _llm_provenance(settings),
                    "spec_approved": False,
                    "spec_approved_at": None,
                    "ir_json": None,
                    "ir_validation": None,
                    "ir_provenance": None,
                    "latest_job_id": None,
                    "assumptions": output.assumptions,
                    "open_questions": output.open_questions,
                    "unsupported_reasons": output.unsupported_reasons,
                    "failure_kind": (
                        "unsupported_design" if output.next_state == "failed" else None
                    ),
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
        if session.open_questions or session.spec.open_questions:
            raise ConflictError(
                "Cannot approve a spec while open questions remain unresolved.",
                code="WIZARD_SPEC_HAS_OPEN_QUESTIONS",
                details={"session_id": session_id},
            )
        if session.unsupported_reasons or session.spec.unsupported_reasons:
            raise ConflictError(
                "Cannot approve a spec that the wizard marked as unsupported.",
                code="WIZARD_SPEC_UNSUPPORTED",
                details={"session_id": session_id},
            )
        underspecified_custom_blocks = [
            block.name
            for block in session.spec.blocks
            if block.block_type == "custom" and not block.required_components
        ]
        if underspecified_custom_blocks:
            raise ConflictError(
                "Cannot approve a spec with underspecified custom blocks.",
                code="WIZARD_SPEC_UNDERSPECIFIED_CUSTOM_BLOCKS",
                details={
                    "session_id": session_id,
                    "block_names": underspecified_custom_blocks,
                },
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
                "failure_kind": None,
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
                "ir_provenance": None,
                "latest_job_id": None,
                "failure_kind": None,
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
    with resource_lock(settings, kind="wizard", resource_id=session_id):
        session = _read_session_for_mutation(settings, session_id)
        retry_failed_ir = (
            _effective_failure_kind(session) == "operational"
            and _failure_operation(session) == "generate_ir"
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
        _assert_llm_provenance_matches(
            settings,
            session,
            operation="generate_ir",
            revision_provenance=session.spec_provenance,
        )
        client = _require_llm_client(llm_client)

        session = session.model_copy(
            update={
                "status": "drafting_ir",
                "failure_kind": None,
                "error": None,
                "updated_at": _utc_now(),
            }
        )
        _persist_session(settings, session)
        last_error: str | None = None
        prior_ir_json: dict[str, object] | None = None
        max_attempts = settings.llm.ir_max_repair_rounds + 1
        try:
            for attempt in range(1, max_attempts + 1):
                try:
                    output = _call_llm_for_json_once(
                        llm_client=client,
                        messages=_build_ir_messages(
                            settings,
                            session,
                            prior_ir_json=prior_ir_json,
                            repair_error=last_error,
                        ),
                        response_model=IrGenerationOutput,
                        attempt=attempt,
                        options=StructuredJsonCallOptions(
                            debug_artifact_writer=_make_debug_artifact_writer(
                                settings, session.id, stage="ir"
                            ),
                        ),
                    )
                except _RepairableStructuredOutputError as exc:
                    last_error = exc.repair_message
                    if attempt >= max_attempts:
                        _raise_structured_output_exhausted(
                            exc, response_model=IrGenerationOutput, attempts=attempt
                        )
                    continue

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
                except UserError as exc:
                    last_error = _format_ir_repair_error(exc)
                    prior_ir_json = output.netlist_json
                    if attempt < max_attempts:
                        continue
                    session = session.model_copy(
                        update={
                            "status": "ir_needs_repair",
                            "ir_json": prior_ir_json,
                            "ir_validation": WizardIrValidation(
                                valid=False, error_message=last_error
                            ),
                            "ir_provenance": _llm_provenance(settings),
                            "latest_job_id": None,
                            "failure_kind": None,
                            "error": None,
                            "updated_at": _utc_now(),
                        }
                    )
                    return _persist_session(settings, session)

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
                        "ir_provenance": _llm_provenance(settings),
                        "latest_job_id": None,
                        "assumptions": sorted(
                            {*(session.assumptions or []), *(output.assumptions or [])}
                        ),
                        "failure_kind": None,
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
            raise AssertionError("IR generation attempt loop exhausted unexpectedly")
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

    error_id: str | None = None
    if isinstance(error, dict):
        error_details = error.get("details")
        if isinstance(error_details, dict):
            candidate_error_id = error_details.get("error_id")
            if isinstance(candidate_error_id, str) and candidate_error_id.startswith("err_"):
                error_id = candidate_error_id

    return WebServiceError(
        "KiCad project generation failed.",
        code="WIZARD_PROJECT_GENERATION_FAILED",
        status_code=status_code,
        details={"session_id": session_id, "job_id": job_id, "operation": "generate_project"},
        error_id=error_id,
    )


def generate_wizard_project(
    *,
    settings: WebSettings,
    session_id: str,
) -> WizardGenerateProjectResponse:
    with resource_lock(settings, kind="wizard", resource_id=session_id):
        session = _read_session_for_mutation(settings, session_id)
        retry_failed_project = (
            _effective_failure_kind(session) in {"generation", "operational"}
            and _failure_operation(session) == "generate_project"
        )
        active_status = session.status in {"ir_ready_for_generation", "completed"}
        if (
            (not active_status and not retry_failed_project)
            or not session.spec_approved
            or session.ir_json is None
            or session.ir_validation is None
            or not session.ir_validation.valid
        ):
            raise ConflictError(
                "Generate current valid Circuit IR before starting project generation.",
                details={"session_id": session_id, "status": session.status},
            )

        session = session.model_copy(
            update={
                "status": "generation_started",
                "failure_kind": None,
                "updated_at": _utc_now(),
                "error": None,
            }
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
            _persist_and_raise_failure(
                settings,
                session,
                exc,
                operation="generate_project",
                failure_kind="generation",
            )

        if job.status != "succeeded":
            project_error = _project_failure_error(
                job.model_dump(mode="json"), session_id=session.id, job_id=job.id
            )
            session = _set_error(
                session,
                _public_error_payload(project_error),
                failure_kind="generation",
            ).model_copy(update={"latest_job_id": job.id})
            LOGGER.info(
                "wizard project generation finished",
                extra={"session_id": session.id, "job_id": job.id, "job_status": job.status},
            )
            _persist_session(settings, session)
            raise project_error

        session = session.model_copy(
            update={
                "status": "completed",
                "failure_kind": None,
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
