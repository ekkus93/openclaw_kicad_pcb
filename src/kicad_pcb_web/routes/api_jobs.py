"""Job API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..deps import get_settings
from ..errors import WebServiceError
from ..schemas import (
    ArtifactListResponse,
    CreateJobFromNetlistRequest,
    JobDetail,
    JobSummary,
)
from ..services.artifacts import list_artifacts, resolve_artifact_path
from ..services.jobs import job_dir_for_id, list_jobs, read_job
from ..services.netlists import generate_project_from_netlist_job
from ..settings import WebSettings

router = APIRouter()


def _read_job_or_404(settings: WebSettings, job_id: str):
    """Load a job or raise a 404 HTTP exception."""

    try:
        return read_job(settings, job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found.") from exc


def _synchronous_generation_error(job: JobDetail) -> WebServiceError:
    """Map a persisted failed synchronous job to truthful HTTP semantics."""

    error_type = job.error.get("type") if isinstance(job.error, dict) else None
    status_code = 500
    if error_type == "tool_error":
        status_code = 503
    elif error_type in {"user_error", "validation_error"}:
        status_code = 422

    error_id: str | None = None
    details: dict[str, object] = {"job_id": job.id, "job_status": job.status}
    if isinstance(job.error, dict):
        error_code = job.error.get("code")
        if isinstance(error_code, str):
            details["job_error_code"] = error_code
        job_error_details = job.error.get("details")
        if isinstance(job_error_details, dict):
            candidate_error_id = job_error_details.get("error_id")
            if isinstance(candidate_error_id, str) and candidate_error_id.startswith("err_"):
                error_id = candidate_error_id

    return WebServiceError(
        "KiCad project generation failed.",
        code="PROJECT_GENERATION_FAILED",
        status_code=status_code,
        details=details,
        error_id=error_id,
    )


@router.post("/jobs/from-netlist", response_model=JobDetail)
def create_job_from_netlist(
    request: CreateJobFromNetlistRequest,
    settings: WebSettings = Depends(get_settings),
) -> JobDetail:
    """Synchronously generate a job from a Circuit IR payload."""

    job = generate_project_from_netlist_job(settings=settings, request=request)
    if job.status != "succeeded":
        raise _synchronous_generation_error(job)
    return job


@router.get("/jobs", response_model=list[JobSummary])
def get_jobs(settings: WebSettings = Depends(get_settings)) -> list[JobSummary]:
    """Return all jobs newest-first."""

    return [record.to_summary() for record in list_jobs(settings)]


@router.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str, settings: WebSettings = Depends(get_settings)) -> JobDetail:
    """Return one job detail payload."""

    record = _read_job_or_404(settings, job_id)
    return record.to_detail(artifacts=list_artifacts(record.work_dir))


@router.get("/jobs/{job_id}/artifacts", response_model=ArtifactListResponse)
def get_job_artifacts(
    job_id: str,
    settings: WebSettings = Depends(get_settings),
) -> ArtifactListResponse:
    """List downloadable artifacts for one job."""

    job_dir = job_dir_for_id(settings, job_id)
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Job not found.")
    return ArtifactListResponse(job_id=job_id, artifacts=list_artifacts(job_dir))


@router.get("/jobs/{job_id}/artifacts/{artifact_name:path}")
def download_job_artifact(
    job_id: str,
    artifact_name: str,
    settings: WebSettings = Depends(get_settings),
) -> FileResponse:
    """Return one artifact file for download."""

    job_dir = job_dir_for_id(settings, job_id)
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Job not found.")
    try:
        artifact_path = resolve_artifact_path(job_dir, artifact_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found.") from exc
    return FileResponse(path=artifact_path, filename=artifact_path.name)
