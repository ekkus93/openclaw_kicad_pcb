"""HTML UI routes."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from kicad_pcb.errors import UserError

from ..deps import get_llm_client, get_settings, get_templates
from ..services.artifacts import list_artifacts
from ..services.jobs import read_job
from ..services.llm import LlmClient
from ..services.wizard import (
    approve_wizard_spec,
    create_wizard_session,
    generate_wizard_ir,
    generate_wizard_project,
    post_wizard_message,
    read_wizard_session,
    update_wizard_session_metadata,
)
from ..settings import WebSettings
from ..wizard_models import CreateWizardSessionRequest, WizardMessageRequest, WizardSessionDetail

router = APIRouter()

WizardStep = Literal["describe", "spec", "ir", "generate"]

_WIZARD_STEP_ORDER: dict[WizardStep, int] = {
    "describe": 0,
    "spec": 1,
    "ir": 2,
    "generate": 3,
}

_WIZARD_STEP_SEQUENCE: tuple[WizardStep, ...] = ("describe", "spec", "ir", "generate")

_WIZARD_STEP_META: dict[WizardStep, dict[str, str]] = {
    "describe": {
        "label": "Describe Circuit",
        "summary": "Start the session and refine the brief.",
        "heading": "Start with the circuit brief.",
        "lead": (
            "Keep the conversation and transcript on this route until the "
            "wizard produces a reviewable spec."
        ),
        "sidebar_title": "Describe Step",
        "sidebar_copy": "Project inputs and transcript stay anchored here.",
        "kicker": "Step 1",
        "title": "Describe the circuit",
        "detail": (
            "Collect the purpose, rails, constraints, and clarifications "
            "before moving forward."
        ),
    },
    "spec": {
        "label": "Review Spec",
        "summary": "Approve the human-readable circuit specification.",
        "heading": "Review the drafted circuit specification.",
        "lead": "This route is the explicit human checkpoint before Circuit IR generation begins.",
        "sidebar_title": "Spec Checkpoint",
        "sidebar_copy": "Approve or revise from a dedicated review page.",
        "kicker": "Step 2",
        "title": "Review the spec",
        "detail": (
            "Confirm the scope and constraints, or send a revision note back "
            "through the conversation loop."
        ),
    },
    "ir": {
        "label": "Review Circuit IR",
        "summary": "Inspect validation, fixes, warnings, and raw IR.",
        "heading": "Validate the generated Circuit IR.",
        "lead": (
            "This route owns validation-first review and any repair loop "
            "before project generation."
        ),
        "sidebar_title": "IR Checkpoint",
        "sidebar_copy": "Validation decides whether generation can proceed.",
        "kicker": "Step 3",
        "title": "Review Circuit IR",
        "detail": (
            "Inspect the validation summary first, then dig into raw JSON "
            "only when you need it."
        ),
    },
    "generate": {
        "label": "Generate Project",
        "summary": (
            "Launch the deterministic generation path and inspect the "
            "result."
        ),
        "heading": "Generate the final project.",
        "lead": (
            "The completed session stays owned by this final route, with job "
            "details one click away."
        ),
        "sidebar_title": "Generation Step",
        "sidebar_copy": "Start generation only from validated Circuit IR.",
        "kicker": "Step 4",
        "title": "Generate the project",
        "detail": "Use the validated IR as the handoff into the deterministic generation pipeline.",
    },
}

_EXAMPLE_NETLIST = {
    "version": "1",
    "components": [
        {"ref": "J1", "symbol": "Connector_Generic:Conn_01x01", "value": "In"},
        {"ref": "R1", "symbol": "Device:R", "value": "10k"},
        {"ref": "J2", "symbol": "Connector_Generic:Conn_01x01", "value": "Out"},
    ],
    "nets": [
        {
            "name": "IN",
            "pins": [
                {"ref": "J1", "pin": "1"},
                {"ref": "R1", "pin": "1"},
            ],
        },
        {
            "name": "OUT",
            "pins": [
                {"ref": "R1", "pin": "2"},
                {"ref": "J2", "pin": "1"},
            ],
        },
    ],
}


def _canonical_wizard_step(session: WizardSessionDetail) -> WizardStep:
    status = session.status
    step: WizardStep = "describe"
    if status in {"drafting_spec", "awaiting_user_clarification"}:
        step = "describe"
    elif status == "spec_ready_for_review":
        step = "spec"
    elif status in {"spec_approved", "drafting_ir", "ir_needs_repair"}:
        step = "ir"
    elif status in {"ir_ready_for_generation", "generation_started", "completed"}:
        step = "generate"
    elif status == "failed":
        if session.ir_json is not None or session.ir_validation is not None:
            step = "generate"
        elif session.spec is not None:
            step = "spec"
    return step


def _wizard_step_unlocked(session: WizardSessionDetail, step: WizardStep) -> bool:
    return _WIZARD_STEP_ORDER[step] <= _WIZARD_STEP_ORDER[_canonical_wizard_step(session)]


def _clean_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _notice_message(exc: ValidationError | UserError) -> str:
    if isinstance(exc, ValidationError):
        first_error = exc.errors()[0]
        return str(first_error.get("msg", "Invalid form input."))
    return str(exc)


def _wizard_step_path(session_id: str, step: WizardStep) -> str:
    return f"/wizard/{session_id}/{step}"


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(url=path, status_code=303)


def _redirect_with_notice(
    path: str,
    *,
    notice: str,
    level: str,
) -> RedirectResponse:
    query = urlencode({"notice": notice, "notice_level": level})
    return _redirect(f"{path}?{query}")


def _read_session_or_404(settings: WebSettings, session_id: str) -> WizardSessionDetail:
    try:
        return read_wizard_session(settings, session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Wizard session not found.") from exc


def _wizard_status_class(status: str) -> str:
    if status in {"spec_ready_for_review", "spec_approved", "drafting_ir", "generation_started"}:
        return "status-active"
    if status in {"completed", "ir_ready_for_generation"}:
        return "status-success"
    if status in {"awaiting_user_clarification", "ir_needs_repair"}:
        return "status-warning"
    if status == "failed":
        return "status-error"
    return "status-neutral"


def _wizard_step_state(
    session: WizardSessionDetail,
    *,
    current_step: WizardStep,
    item_step: WizardStep,
) -> str:
    if item_step == current_step:
        return "active"
    if not _wizard_step_unlocked(session, item_step):
        return "upcoming"
    return "complete"


def _previous_step(step: WizardStep) -> WizardStep | None:
    index = _WIZARD_STEP_ORDER[step]
    if index == 0:
        return None
    return _WIZARD_STEP_SEQUENCE[index - 1]


def _continue_step(session: WizardSessionDetail, current_step: WizardStep) -> WizardStep | None:
    canonical_step = _canonical_wizard_step(session)
    next_index = _WIZARD_STEP_ORDER[current_step] + 1
    if next_index > _WIZARD_STEP_ORDER[canonical_step]:
        return None
    return _WIZARD_STEP_SEQUENCE[next_index]


def _banner_for_step(session: WizardSessionDetail, step: WizardStep) -> tuple[str, str]:
    banner_class = "status-neutral"
    banner_text = "Follow the workflow one checkpoint at a time."
    if session.status == "awaiting_user_clarification":
        banner_class = "status-warning"
        banner_text = "The wizard needs more detail before a spec can be reviewed."
    elif session.status == "spec_ready_for_review":
        banner_class = "status-active"
        banner_text = "The spec is ready for review and explicit approval."
    elif session.status == "spec_approved":
        banner_class = "status-active"
        banner_text = "The spec is approved. Circuit IR generation is now available."
    elif session.status == "ir_needs_repair":
        banner_class = "status-warning"
        banner_text = (
            "The latest Circuit IR draft failed validation and needs another "
            "repair pass."
        )
    elif session.status == "ir_ready_for_generation":
        banner_class = "status-success"
        banner_text = (
            "Validated Circuit IR is ready for deterministic project "
            "generation."
        )
    elif session.status == "completed":
        banner_class = "status-success"
        banner_text = (
            "Project generation completed. This final route now owns the "
            "session result."
        )
    elif session.status == "failed":
        banner_class = "status-error"
        if step == "generate" and session.latest_job_id:
            banner_text = (
                "The latest generation run failed. Review the job details "
                "before retrying."
            )
        else:
            banner_text = (
                "The wizard hit an error. Review the session details on this "
                "route."
            )
    return (banner_class, banner_text)


def _invalidation_notice(session: WizardSessionDetail, step: WizardStep) -> str | None:
    if step == "describe" and (
        session.spec is not None or session.ir_json is not None or session.latest_job_id is not None
    ):
        return (
            "Submitting another conversation message clears spec approval, "
            "Circuit IR, and any active generation result for this session."
        )
    if step == "spec" and (session.ir_json is not None or session.latest_job_id is not None):
        return (
            "Sending a revision note from spec review clears the current "
            "Circuit IR and generation result."
        )
    if step == "ir" and session.latest_job_id is not None:
        return (
            "Generating Circuit IR again clears the previously linked "
            "generation job from the active wizard session."
        )
    return None


def _render_wizard_step(
    request: Request,
    *,
    settings: WebSettings,
    session: WizardSessionDetail,
    step: WizardStep,
) -> HTMLResponse:
    templates = get_templates()
    canonical_step = _canonical_wizard_step(session)
    previous_step = _previous_step(step)
    continue_step = _continue_step(session, step)
    latest_job: dict[str, Any] | None = None
    if session.latest_job_id is not None:
        try:
            latest_job_record = read_job(settings, session.latest_job_id)
            latest_job = latest_job_record.to_detail(
                artifacts=list_artifacts(latest_job_record.work_dir)
            ).model_dump(mode="json")
        except FileNotFoundError:
            latest_job = None

    banner_class, banner_text = _banner_for_step(session, step)
    step_items: list[dict[str, str | None]] = []
    for item_step in _WIZARD_STEP_SEQUENCE:
        step_items.append(
            {
                "name": item_step,
                "label": _WIZARD_STEP_META[item_step]["label"],
                "summary": _WIZARD_STEP_META[item_step]["summary"],
                "url": _wizard_step_path(session.id, item_step)
                if _wizard_step_unlocked(session, item_step)
                else None,
                "state": _wizard_step_state(session, current_step=step, item_step=item_step),
            }
        )

    approve_note = (
        "Approve the spec after all open questions and unsupported "
        "constraints are resolved."
    )
    can_approve_spec = (
        session.spec is not None
        and not session.spec_approved
        and not session.open_questions
        and not session.unsupported_reasons
    )
    can_generate_ir = session.spec is not None and session.spec_approved
    can_generate_project = bool(session.ir_validation and session.ir_validation.valid)

    context = {
        "page_title": f"LLM Wizard - {_WIZARD_STEP_META[step]['label']}",
        "llm_provider": settings.llm.provider,
        "llm_enabled": settings.llm.enabled,
        "session": session,
        "current_step": step,
        "current_step_meta": _WIZARD_STEP_META[step],
        "canonical_step": canonical_step,
        "canonical_step_meta": _WIZARD_STEP_META[canonical_step],
        "canonical_step_url": _wizard_step_path(session.id, canonical_step),
        "status_class": _wizard_status_class(session.status),
        "banner_class": banner_class,
        "banner_text": banner_text,
        "step_items": step_items,
        "wizard_notice": request.query_params.get("notice"),
        "wizard_notice_level": request.query_params.get("notice_level", "status-neutral"),
        "invalidation_notice": _invalidation_notice(session, step),
        "previous_step_url": (
            _wizard_step_path(session.id, previous_step) if previous_step is not None else "/wizard"
        ),
        "previous_step_label": (
            f"Back to {_WIZARD_STEP_META[previous_step]['label']}"
            if previous_step is not None
            else "Back to Start"
        ),
        "continue_url": (
            _wizard_step_path(session.id, continue_step) if continue_step is not None else None
        ),
        "continue_label": (
            f"Continue to {_WIZARD_STEP_META[continue_step]['label']}"
            if continue_step is not None
            else None
        ),
        "continue_note": None,
        "describe_submit_label": (
            "Send Revision Note" if len(session.messages) > 0 else "Start Wizard"
        ),
        "can_approve_spec": can_approve_spec,
        "approve_note": approve_note,
        "can_generate_ir": can_generate_ir,
        "generate_ir_label": (
            "Repair Circuit IR" if session.status == "ir_needs_repair" else "Generate Circuit IR"
        ),
        "generate_ir_note": "Approve the spec before generating Circuit IR.",
        "can_generate_project": can_generate_project,
        "generate_project_label": (
            "Generate Again" if latest_job is not None else "Generate Project"
        ),
        "generate_project_note": "Wait for a valid Circuit IR before starting project generation.",
        "latest_job": latest_job,
    }

    if step == "describe" and continue_step is None:
        context["continue_note"] = (
            "Continue becomes available when the wizard produces a reviewable "
            "spec."
        )
    elif step == "spec" and continue_step is None:
        context["continue_note"] = approve_note
    elif step == "ir" and continue_step is None:
        context["continue_note"] = (
            "Continue becomes available after the IR validates successfully."
        )
    elif step == "generate" and latest_job is None:
        context["continue_note"] = (
            "This final route remains active after generation starts or "
            "finishes."
        )

    return templates.TemplateResponse(request, f"wizard_{step}.html", context)


@router.get("/")
async def index(
    request: Request,
    settings: WebSettings = Depends(get_settings),
) -> RedirectResponse:
    """Redirect the site root to the wizard landing page."""

    del request
    del settings
    return _redirect("/wizard")


@router.get("/wizard", response_class=HTMLResponse)
async def wizard_page(
    request: Request,
    settings: WebSettings = Depends(get_settings),
) -> HTMLResponse:
    """Render the LLM-assisted wizard page."""

    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "wizard.html",
        {
            "page_title": "LLM Wizard",
            "llm_provider": settings.llm.provider,
            "llm_enabled": settings.llm.enabled,
            "wizard_notice": request.query_params.get("notice"),
            "wizard_notice_level": request.query_params.get("notice_level", "status-neutral"),
        },
    )


@router.post("/wizard/start")
async def wizard_start(
    request: Request,
    settings: WebSettings = Depends(get_settings),
    llm_client: LlmClient | None = Depends(get_llm_client),
) -> RedirectResponse:
    """Create a new wizard session from the landing page."""

    form = await request.form()
    try:
        create_request = CreateWizardSessionRequest(
            message=str(form.get("message", "")).strip(),
            project_name=_clean_optional_text(form.get("project_name")),
            symbols_dir=_clean_optional_text(form.get("symbols_dir")),
        )
        session = create_wizard_session(
            settings=settings,
            request=create_request,
            llm_client=llm_client,
        )
    except (UserError, ValidationError) as exc:
        return _redirect_with_notice(
            "/wizard",
            notice=_notice_message(exc),
            level="status-error",
        )

    return _redirect_with_notice(
        _wizard_step_path(session.id, _canonical_wizard_step(session)),
        notice=(
            "Wizard session failed. Review the session details."
            if session.status == "failed"
            else "Wizard session created."
        ),
        level="status-error" if session.status == "failed" else "status-active",
    )


@router.get("/wizard/{session_id}")
async def wizard_session_redirect(
    session_id: str,
    settings: WebSettings = Depends(get_settings),
) -> RedirectResponse:
    """Redirect a session root URL to its canonical step page."""

    session = _read_session_or_404(settings, session_id)
    return _redirect(_wizard_step_path(session.id, _canonical_wizard_step(session)))


@router.get("/wizard/{session_id}/{step}", response_class=HTMLResponse)
async def wizard_step_page(
    request: Request,
    session_id: str,
    step: WizardStep,
    settings: WebSettings = Depends(get_settings),
) -> Response:
    """Render one route-specific wizard step page."""

    session = _read_session_or_404(settings, session_id)
    if not _wizard_step_unlocked(session, step):
        return _redirect(_wizard_step_path(session.id, _canonical_wizard_step(session)))
    return _render_wizard_step(request, settings=settings, session=session, step=step)


async def _handle_message_post(
    request: Request,
    *,
    session_id: str,
    current_step: WizardStep,
    settings: WebSettings,
    llm_client: LlmClient | None,
) -> RedirectResponse:
    form = await request.form()
    current_path = _wizard_step_path(session_id, current_step)
    try:
        update_wizard_session_metadata(
            settings=settings,
            session_id=session_id,
            project_name=_clean_optional_text(form.get("project_name")),
            symbols_dir=_clean_optional_text(form.get("symbols_dir")),
        )
        message_request = WizardMessageRequest(message=str(form.get("message", "")).strip())
        session = post_wizard_message(
            settings=settings,
            session_id=session_id,
            request=message_request,
            llm_client=llm_client,
        )
    except (UserError, ValidationError) as exc:
        return _redirect_with_notice(
            current_path,
            notice=_notice_message(exc),
            level="status-error",
        )

    return _redirect_with_notice(
        _wizard_step_path(session.id, _canonical_wizard_step(session)),
        notice=(
            "Wizard update failed. Review the session details."
            if session.status == "failed"
            else "Conversation updated."
        ),
        level="status-error" if session.status == "failed" else "status-active",
    )


@router.post("/wizard/{session_id}/describe/message")
async def wizard_describe_message(
    request: Request,
    session_id: str,
    settings: WebSettings = Depends(get_settings),
    llm_client: LlmClient | None = Depends(get_llm_client),
) -> RedirectResponse:
    """Post a describe-step message and redirect to the canonical next route."""

    return await _handle_message_post(
        request,
        session_id=session_id,
        current_step="describe",
        settings=settings,
        llm_client=llm_client,
    )


@router.post("/wizard/{session_id}/spec/revise")
async def wizard_spec_revise(
    request: Request,
    session_id: str,
    settings: WebSettings = Depends(get_settings),
    llm_client: LlmClient | None = Depends(get_llm_client),
) -> RedirectResponse:
    """Post a revision note from the spec step."""

    return await _handle_message_post(
        request,
        session_id=session_id,
        current_step="spec",
        settings=settings,
        llm_client=llm_client,
    )


@router.post("/wizard/{session_id}/spec/approve")
async def wizard_spec_approve(
    session_id: str,
    settings: WebSettings = Depends(get_settings),
) -> RedirectResponse:
    """Approve a reviewable spec and advance to the IR step."""

    current_path = _wizard_step_path(session_id, "spec")
    try:
        session = approve_wizard_spec(settings=settings, session_id=session_id)
    except UserError as exc:
        return _redirect_with_notice(
            current_path,
            notice=str(exc),
            level="status-error",
        )

    return _redirect_with_notice(
        _wizard_step_path(session.id, _canonical_wizard_step(session)),
        notice="Spec approved.",
        level="status-success",
    )


@router.post("/wizard/{session_id}/ir/generate")
async def wizard_ir_generate(
    session_id: str,
    settings: WebSettings = Depends(get_settings),
    llm_client: LlmClient | None = Depends(get_llm_client),
) -> RedirectResponse:
    """Generate or repair Circuit IR and redirect to the authoritative step page."""

    current_path = _wizard_step_path(session_id, "ir")
    try:
        session = generate_wizard_ir(
            settings=settings,
            session_id=session_id,
            llm_client=llm_client,
        )
    except UserError as exc:
        return _redirect_with_notice(
            current_path,
            notice=str(exc),
            level="status-error",
        )

    notice = "Circuit IR is ready for generation."
    level = "status-success"
    if session.status == "ir_needs_repair":
        notice = "Circuit IR still needs repair."
        level = "status-warning"
    elif session.status == "failed":
        notice = "Circuit IR generation failed."
        level = "status-error"

    return _redirect_with_notice(
        _wizard_step_path(session.id, _canonical_wizard_step(session)),
        notice=notice,
        level=level,
    )


@router.post("/wizard/{session_id}/generate/project")
async def wizard_project_generate(
    session_id: str,
    settings: WebSettings = Depends(get_settings),
) -> RedirectResponse:
    """Start deterministic project generation from validated IR."""

    current_path = _wizard_step_path(session_id, "generate")
    try:
        response = generate_wizard_project(settings=settings, session_id=session_id)
    except UserError as exc:
        return _redirect_with_notice(
            current_path,
            notice=str(exc),
            level="status-error",
        )

    session = response.session
    success = session.status == "completed"
    return _redirect_with_notice(
        _wizard_step_path(session.id, "generate"),
        notice="Project generation completed." if success else "Project generation failed.",
        level="status-success" if success else "status-error",
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(
    request: Request,
    job_id: str,
    settings: WebSettings = Depends(get_settings),
) -> HTMLResponse:
    """Render the job detail page."""

    templates = get_templates()
    try:
        record = read_job(settings, job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found.") from exc

    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "page_title": f"Job {job_id}",
            "job": record.to_detail(artifacts=list_artifacts(record.work_dir)),
        },
    )
