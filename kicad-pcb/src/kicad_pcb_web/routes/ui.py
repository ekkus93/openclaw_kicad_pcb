"""HTML UI routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..deps import get_templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Render the home page."""

    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "page_title": "KiCad PCB Web App",
        },
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(request: Request, job_id: str) -> HTMLResponse:
    """Render a placeholder job detail page."""

    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "page_title": f"Job {job_id}",
            "job_id": job_id,
        },
    )
