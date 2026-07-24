"""Wizard API routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_llm_client, get_settings
from ..services.llm import LlmClient
from ..services.wizard import (
    approve_wizard_spec,
    clear_wizard_ir,
    create_wizard_session,
    generate_wizard_ir,
    generate_wizard_project,
    post_wizard_message,
    read_wizard_session,
)
from ..settings import WebSettings
from ..wizard_models import (
    CreateWizardSessionRequest,
    WizardGenerateProjectResponse,
    WizardMessageRequest,
    WizardSessionDetail,
)

router = APIRouter()
LOGGER = logging.getLogger("uvicorn.error")


def _read_session_or_404(settings: WebSettings, session_id: str) -> WizardSessionDetail:
    try:
        return read_wizard_session(settings, session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Wizard session not found.") from exc


@router.post("/wizard/sessions", response_model=WizardSessionDetail)
def create_session(
    request: CreateWizardSessionRequest,
    settings: WebSettings = Depends(get_settings),
    llm_client: LlmClient | None = Depends(get_llm_client),
) -> WizardSessionDetail:
    LOGGER.info(
        "wizard create request received",
        extra={
            "provider": settings.llm.provider,
            "has_project_name": request.project_name is not None,
            "has_symbols_dir": request.symbols_dir is not None,
            "message_length": len(request.message),
        },
    )
    return create_wizard_session(settings=settings, request=request, llm_client=llm_client)


@router.get("/wizard/sessions/{session_id}", response_model=WizardSessionDetail)
def get_session(
    session_id: str,
    settings: WebSettings = Depends(get_settings),
) -> WizardSessionDetail:
    return _read_session_or_404(settings, session_id)


@router.post("/wizard/sessions/{session_id}/messages", response_model=WizardSessionDetail)
def add_message(
    session_id: str,
    request: WizardMessageRequest,
    settings: WebSettings = Depends(get_settings),
    llm_client: LlmClient | None = Depends(get_llm_client),
) -> WizardSessionDetail:
    return post_wizard_message(
        settings=settings,
        session_id=session_id,
        request=request,
        llm_client=llm_client,
    )


@router.post("/wizard/sessions/{session_id}/approve-spec", response_model=WizardSessionDetail)
def approve_spec(
    session_id: str,
    settings: WebSettings = Depends(get_settings),
) -> WizardSessionDetail:
    return approve_wizard_spec(settings=settings, session_id=session_id)


@router.post("/wizard/sessions/{session_id}/clear-ir", response_model=WizardSessionDetail)
def clear_ir(
    session_id: str,
    settings: WebSettings = Depends(get_settings),
) -> WizardSessionDetail:
    return clear_wizard_ir(settings=settings, session_id=session_id)


@router.post("/wizard/sessions/{session_id}/generate-ir", response_model=WizardSessionDetail)
def generate_ir(
    session_id: str,
    settings: WebSettings = Depends(get_settings),
    llm_client: LlmClient | None = Depends(get_llm_client),
) -> WizardSessionDetail:
    return generate_wizard_ir(settings=settings, session_id=session_id, llm_client=llm_client)


@router.post(
    "/wizard/sessions/{session_id}/generate-project",
    response_model=WizardGenerateProjectResponse,
)
def generate_project(
    session_id: str,
    settings: WebSettings = Depends(get_settings),
) -> WizardGenerateProjectResponse:
    return generate_wizard_project(settings=settings, session_id=session_id)
