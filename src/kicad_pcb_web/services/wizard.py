"""Wizard orchestration for LLM-assisted circuit design sessions."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from kicad_pcb.errors import UserError

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
from ._wizard_llm import (  # noqa: F401
    _build_ir_messages,
    _build_spec_messages,
    _call_llm_for_json,
    _format_ir_repair_error,
    _require_llm_client,
)
from ._wizard_session_io import (  # noqa: F401
    _append_message,
    _make_debug_artifact_writer,
    _new_session_id,
    _persist_session,
    _set_error,
    _utc_now,
    read_wizard_session,
    update_wizard_session_metadata,
)
from .llm import LlmClient
from .netlists import generate_project_from_netlist_job, prepare_netlist_dict

LOGGER = logging.getLogger("uvicorn.error")


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
                settings,
                session.id,
                stage="spec",
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
        LOGGER.warning(
            "wizard spec draft failed",
            extra={
                "session_id": session.id,
                "provider": settings.llm.provider,
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
                "error_type": type(exc).__name__,
            },
        )
        failed = _set_error(session, exc)
        return _persist_session(settings, failed)


def post_wizard_message(
    *,
    settings: WebSettings,
    session_id: str,
    request: WizardMessageRequest,
    llm_client: LlmClient | None,
) -> WizardSessionDetail:
    client = _require_llm_client(llm_client)
    session = read_wizard_session(settings, session_id)
    session = session.model_copy(
        update={
            "status": "drafting_spec",
            "spec_approved": False,
            "spec_approved_at": None,
            "ir_json": None,
            "ir_validation": None,
            "latest_job_id": None,
            "error": None,
            "unsupported_reasons": [],
        }
    )
    session = _append_message(session, role="user", content=request.message)
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
                settings,
                session.id,
                stage="spec",
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
        LOGGER.warning(
            "wizard spec revision failed",
            extra={
                "session_id": session.id,
                "provider": settings.llm.provider,
                "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
                "error_type": type(exc).__name__,
            },
        )
        failed = _set_error(session, exc)
        return _persist_session(settings, failed)


def approve_wizard_spec(*, settings: WebSettings, session_id: str) -> WizardSessionDetail:
    session = read_wizard_session(settings, session_id)
    if session.spec is None:
        raise UserError("Cannot approve a wizard session before a spec draft exists.")
    if session.unsupported_reasons:
        raise UserError("Cannot approve a spec that the wizard marked as unsupported.")
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
    session = read_wizard_session(settings, session_id)
    if not session.spec_approved:
        raise UserError("Cannot clear Circuit IR on a session whose spec is not approved.")
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
    session = read_wizard_session(settings, session_id)
    if session.spec is None or not session.spec_approved:
        raise UserError("Approve the circuit spec before generating Circuit IR.")

    session = session.model_copy(
        update={
            "status": "drafting_ir",
            "latest_job_id": None,
            "error": None,
            "updated_at": _utc_now(),
        }
    )
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
                    settings,
                    session.id,
                    stage="ir",
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
                    session,
                    role="assistant",
                    content=output.assistant_message,
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
                "updated_at": _utc_now(),
            }
        )
        return _persist_session(settings, session)
    except Exception as exc:
        failed = _set_error(session, exc)
        return _persist_session(settings, failed)


def generate_wizard_project(
    *,
    settings: WebSettings,
    session_id: str,
) -> WizardGenerateProjectResponse:
    session = read_wizard_session(settings, session_id)
    if session.ir_json is None or session.ir_validation is None or not session.ir_validation.valid:
        raise UserError("Generate valid Circuit IR before starting project generation.")

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
    session = session.model_copy(
        update={
            "status": "completed" if job.status == "succeeded" else "failed",
            "latest_job_id": job.id,
            "updated_at": _utc_now(),
        }
    )
    LOGGER.info(
        "wizard project generation finished",
        extra={"session_id": session.id, "job_id": job.id, "job_status": job.status},
    )
    session = _persist_session(settings, session)
    return WizardGenerateProjectResponse(session=session, job=job.model_dump(mode="json"))
