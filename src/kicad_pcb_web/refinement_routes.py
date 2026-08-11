"""Opt-in FastAPI router for bounded schematic refinement."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException

from kicad_pcb.errors import UserError

from .services.configured_refinement import run_configured_refinement_request
from .services.refinement_api import RefinementRunRequest, RefinementRunResponse
from .services.refinement_config import RefinementFeatureConfig
from .services.schematic_refinement import RefinementRuntime


@dataclass(frozen=True)
class RefinementRouteDependencies:
    """Server-owned dependencies; request payloads cannot override these values."""

    accepted_path: Callable[[], Path]
    runtime: Callable[[], RefinementRuntime]


def build_refinement_router(
    *,
    config: RefinementFeatureConfig,
    dependencies: RefinementRouteDependencies,
) -> APIRouter:
    """Return an empty router while disabled, otherwise mount the single safe mutation path."""

    router = APIRouter()
    if not config.enabled:
        return router

    @router.post("/api/refinement/run", response_model=RefinementRunResponse)
    def run_refinement(request: RefinementRunRequest) -> RefinementRunResponse:
        try:
            return run_configured_refinement_request(
                accepted_path=dependencies.accepted_path(),
                runtime=dependencies.runtime(),
                request=request,
                config=config,
            )
        except UserError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": str(exc.code), "message": str(exc)},
            ) from exc

    return router
