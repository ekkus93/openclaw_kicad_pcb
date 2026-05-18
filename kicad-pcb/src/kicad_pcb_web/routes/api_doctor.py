"""Doctor API routes."""

from __future__ import annotations

import sys

from fastapi import APIRouter, Depends

from ..deps import get_settings
from ..schemas import DoctorCheck, DoctorResponse
from ..settings import WebSettings

router = APIRouter()


@router.get("/doctor", response_model=DoctorResponse)
async def doctor(settings: WebSettings = Depends(get_settings)) -> DoctorResponse:
    """Return a minimal doctor payload for bootstrap validation."""

    checks = [
        DoctorCheck(
            name="python",
            ok=sys.version_info >= (3, 11),
            detail=(
                f"Python {sys.version_info.major}."
                f"{sys.version_info.minor}."
                f"{sys.version_info.micro}"
            ),
        ),
        DoctorCheck(
            name="jobs_dir",
            ok=settings.jobs_dir.exists() and settings.jobs_dir.is_dir(),
            detail=str(settings.jobs_dir),
        ),
    ]
    return DoctorResponse(ok=all(check.ok for check in checks), checks=checks)
