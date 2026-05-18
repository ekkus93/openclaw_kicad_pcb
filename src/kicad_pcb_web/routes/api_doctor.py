"""Doctor API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import get_settings
from ..schemas import DoctorResponse
from ..services.doctor import run_doctor
from ..settings import WebSettings

router = APIRouter()


@router.get("/doctor", response_model=DoctorResponse)
async def doctor(settings: WebSettings = Depends(get_settings)) -> DoctorResponse:
    """Return the backend health report."""

    return run_doctor(settings)
