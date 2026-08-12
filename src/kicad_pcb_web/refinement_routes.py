"""Opt-in FastAPI router for bounded schematic refinement."""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from kicad_pcb.errors import UserError

from .deps import get_llm_client, get_settings
from .services.llm import LlmClient
from .services.refinement_api import RefinementRunRequest, RefinementRunResponse
from .services.refinement_config import RefinementFeatureConfig
from .services.wizard_refinement import run_wizard_refinement_request
from .settings import WebSettings


def build_refinement_router(*, config: RefinementFeatureConfig) -> APIRouter:
    """Return an empty router while disabled, otherwise mount the trusted wizard path."""

    router = APIRouter()
    if not config.enabled:
        return router

    @router.post("/api/refinement/run", response_model=RefinementRunResponse)
    async def run_refinement(
        http_request: Request,
        settings: WebSettings = Depends(get_settings),
        llm_client: LlmClient | None = Depends(get_llm_client),
    ) -> RefinementRunResponse:
        request = await _validated_request(http_request)
        try:
            return await run_in_threadpool(
                run_wizard_refinement_request,
                settings=settings,
                llm_client=llm_client,
                request=request,
                config=config,
            )
        except UserError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": _error_code(exc),
                    "message": "Schematic refinement request could not be completed.",
                },
            ) from exc

    return router


def install_refinement_routes(
    app: FastAPI,
    *,
    config: RefinementFeatureConfig,
) -> None:
    """Install refinement routes only when the validated feature configuration enables them."""

    app.include_router(build_refinement_router(config=config))


async def _validated_request(http_request: Request) -> RefinementRunRequest:
    try:
        payload = await http_request.json()
        return RefinementRunRequest.model_validate(payload)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "REFINEMENT_INVALID_REQUEST",
                "message": "Invalid schematic refinement request.",
            },
        ) from exc


def _error_code(exc: UserError) -> str:
    value = exc.code
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)
