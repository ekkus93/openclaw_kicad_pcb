"""Job workspace services."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from kicad_pcb.errors import ErrorCode, UserError

from ..schemas import JobDetail, JobStatus, JobSummary
from ..settings import WebSettings

_UNSAFE_JOB_ID_PARTS = ("..", "/", "\\")
_SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_UNSAFE_PROJECT_CHARS_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")


def _utc_now() -> str:
    """Return the current UTC timestamp as an ISO 8601 string."""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sanitize_project_name(name: str) -> str:
    """Normalize a project name to a filesystem-safe directory slug."""

    normalized = _UNSAFE_PROJECT_CHARS_RE.sub("_", name.strip().replace(" ", "_"))
    normalized = _MULTI_UNDERSCORE_RE.sub("_", normalized).strip("._-")
    if not normalized:
        raise UserError(
            "Project name resolves to an empty filesystem-safe name.",
            code=ErrorCode.USER_ERROR,
        )
    return normalized


def new_job_id() -> str:
    """Return a new server-generated job id."""

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{uuid4().hex[:8]}"


def validate_job_id(job_id: str) -> str:
    """Reject empty and path-like job ids."""

    if not job_id or any(part in job_id for part in _UNSAFE_JOB_ID_PARTS):
        raise UserError(
            f"Unsafe job id: {job_id!r}",
            code=ErrorCode.USER_ERROR,
            details={"job_id": job_id},
        )
    if not _SAFE_JOB_ID_RE.fullmatch(job_id):
        raise UserError(
            f"Unsafe job id: {job_id!r}",
            code=ErrorCode.USER_ERROR,
            details={"job_id": job_id},
        )
    return job_id


def job_dir_for_id(settings: WebSettings, job_id: str) -> Path:
    """Return the resolved job directory for a validated job id."""

    return settings.jobs_dir / validate_job_id(job_id)


@dataclass(frozen=True)
class JobRecord:
    """File-backed job metadata."""

    id: str
    status: JobStatus
    project_name: str
    created_at: str
    updated_at: str
    work_dir: Path
    input_path: Path
    project_dir: Path
    artifacts_dir: Path
    request: dict[str, Any]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @property
    def job_json_path(self) -> Path:
        """Return the canonical job-state file path."""

        return self.work_dir / "job.json"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable record."""

        return {
            "id": self.id,
            "status": self.status,
            "project_name": self.project_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "work_dir": str(self.work_dir),
            "input_path": str(self.input_path),
            "project_dir": str(self.project_dir),
            "artifacts_dir": str(self.artifacts_dir),
            "request": self.request,
            "result": self.result,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobRecord:
        """Build a record from persisted JSON data."""

        return cls(
            id=str(data["id"]),
            status=cast(JobStatus, data["status"]),
            project_name=str(data["project_name"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            work_dir=Path(data["work_dir"]),
            input_path=Path(data["input_path"]),
            project_dir=Path(data["project_dir"]),
            artifacts_dir=Path(data["artifacts_dir"]),
            request=dict(data.get("request", {})),
            result=dict(data["result"]) if isinstance(data.get("result"), dict) else None,
            error=dict(data["error"]) if isinstance(data.get("error"), dict) else None,
        )

    def to_summary(self) -> JobSummary:
        """Return the summary API model for this record."""

        return JobSummary(
            id=self.id,
            status=self.status,
            project_name=self.project_name,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def to_detail(self, *, artifacts: list[str] | None = None) -> JobDetail:
        """Return the detail API model for this record."""

        return JobDetail(
            id=self.id,
            status=self.status,
            project_name=self.project_name,
            created_at=self.created_at,
            updated_at=self.updated_at,
            request=self.request,
            result=self.result,
            error=self.error,
            artifacts=artifacts or [],
        )


def write_job(record: JobRecord) -> None:
    """Persist the canonical private job-state file."""

    payload = json.dumps(record.to_dict(), indent=2, sort_keys=True)
    record.job_json_path.write_text(payload, encoding="utf-8")


def create_job_workspace(
    settings: WebSettings,
    project_name: str,
    request: dict[str, Any],
) -> JobRecord:
    """Create and persist a new isolated job workspace."""

    job_id = new_job_id()
    safe_project_name = sanitize_project_name(project_name)
    work_dir = settings.jobs_dir / job_id
    input_dir = work_dir / "input"
    project_root_dir = work_dir / "project"
    artifacts_dir = work_dir / "artifacts"

    input_dir.mkdir(parents=True, exist_ok=False)
    project_root_dir.mkdir(parents=True, exist_ok=False)
    artifacts_dir.mkdir(parents=True, exist_ok=False)

    now = _utc_now()
    record = JobRecord(
        id=job_id,
        status="queued",
        project_name=safe_project_name,
        created_at=now,
        updated_at=now,
        work_dir=work_dir,
        input_path=input_dir / "circuit_ir.json",
        project_dir=project_root_dir / safe_project_name,
        artifacts_dir=artifacts_dir,
        request=request,
    )
    write_job(record)
    return record


def read_job(settings: WebSettings, job_id: str) -> JobRecord:
    """Load one job record by id."""

    job_dir = job_dir_for_id(settings, job_id)
    job_json_path = job_dir / "job.json"
    if not job_json_path.is_file():
        raise FileNotFoundError(job_json_path)
    return JobRecord.from_dict(json.loads(job_json_path.read_text(encoding="utf-8")))


def list_jobs(settings: WebSettings) -> list[JobRecord]:
    """Return persisted jobs sorted newest first."""

    records: list[JobRecord] = []
    if not settings.jobs_dir.exists():
        return records

    for child in settings.jobs_dir.iterdir():
        if not child.is_dir():
            continue
        job_json_path = child / "job.json"
        if not job_json_path.is_file():
            continue
        records.append(JobRecord.from_dict(json.loads(job_json_path.read_text(encoding="utf-8"))))

    return sorted(records, key=lambda record: (record.created_at, record.id), reverse=True)


def update_job_status(
    record: JobRecord,
    *,
    status: JobStatus,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> JobRecord:
    """Update and persist a job status transition."""

    updated = replace(
        record,
        status=status,
        updated_at=_utc_now(),
        result=result if result is not None else record.result,
        error=error if error is not None else record.error,
    )
    write_job(updated)
    return updated
