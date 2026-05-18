"""HTML UI routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..deps import get_settings, get_templates
from ..services.artifacts import list_artifacts
from ..services.jobs import list_jobs, read_job
from ..settings import WebSettings

router = APIRouter()

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


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    settings: WebSettings = Depends(get_settings),
) -> HTMLResponse:
    """Render the home page."""

    templates = get_templates()
    recent_jobs = [record.to_summary() for record in list_jobs(settings)[:10]]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "page_title": "KiCad PCB Web App",
            "example_netlist_json": json.dumps(_EXAMPLE_NETLIST, indent=2),
            "recent_jobs": recent_jobs,
        },
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
