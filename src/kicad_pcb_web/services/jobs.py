"""Job workspace services."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from pydantic import ValidationError

from kicad_pcb.errors import ErrorCode, UserError

from ..errors import PersistedStateError
from ..schemas import JobDetail, JobStatus, JobSummary
from ..settings import WebSettings
from .atomic_io import atomic_write_json
from .resource_locks import resource_lock

LOGGER = logging.getLogger("uvicorn.error")
_UNSAFE_JOB_ID_PARTS = ("..", "/", "\\")
_SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_UNSAFE_PROJECT_CHARS_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")
WIZARD_SESSION_OWNER_REQUEST_KEY = "_wizard_session_id"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sanitize_project_name(name: str) -> str:
    normalized = _UNSAFE_PROJECT_CHARS_RE.sub("_", name.strip().replace(" ", "_"))
    normalized = _MULTI_UNDERSCORE_RE.sub("_", normalized).strip("._-")
    if not normalized:
        raise UserError(
            "Project name resolves to an empty filesystem-safe name.",
            code=ErrorCode.USER_ERROR,
        )
    return normalized


def new_job_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{uuid4().hex[:8]}"


def validate_job_id(job_id: str) -> str:
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
        return self.work_dir / "job.json"

    def to_dict(self) -> dict[str, Any]:
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
        return JobSummary(
            id=self.id,
            status=self.status,
            project_name=self.project_name,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def to_detail(self, *, artifacts: list[str] | None = None) -> JobDetail:
        public_request = {
            key: value
            for key, value in self.request.items()
            if key != WIZARD_SESSION_OWNER_REQUEST_KEY
        }
        return JobDetail(
            id=self.id,
            status=self.status,
            project_name=self.project_name,
            created_at=self.created_at,
            updated_at=self.updated_at,
            request=public_request,
            result=self.result,
            error=self.error,
            artifacts=artifacts or [],
        )


def _write_job_unlocked(record: JobRecord) -> None:
    atomic_write_json(record.job_json_path, record.to_dict())


def write_job(settings: WebSettings, record: JobRecord) -> None:
    """Persist the canonical private job-state file under a bounded lock."""

    with resource_lock(settings, kind="jobs", resource_id=record.id):
        _write_job_unlocked(record)


def create_job_workspace(
    settings: WebSettings,
    project_name: str,
    request: dict[str, Any],
) -> JobRecord:
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
    write_job(settings, record)
    return record


def _canonicalize_job_paths(record: JobRecord, *, job_json_path: Path) -> JobRecord:
    """Derive workspace paths from the canonical state location instead of trusting JSON paths."""

    work_dir = job_json_path.parent.resolve()
    return replace(
        record,
        work_dir=work_dir,
        input_path=work_dir / "input" / "circuit_ir.json",
        project_dir=work_dir / "project" / record.project_name,
        artifacts_dir=work_dir / "artifacts",
    )


def _decode_job_record(job_json_path: Path, *, expected_id: str) -> JobRecord:
    try:
        payload = json.loads(job_json_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("job state must be a JSON object")
        record = JobRecord.from_dict(payload)
        if sanitize_project_name(record.project_name) != record.project_name:
            raise ValueError("persisted project name is not canonical")
        record = _canonicalize_job_paths(record, job_json_path=job_json_path)
        record.to_detail()
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        ValidationError,
        UserError,
    ) as exc:
        LOGGER.error(
            "invalid persisted job state",
            extra={
                "job_id": expected_id,
                "state_file": job_json_path.name,
                "error_type": type(exc).__name__,
            },
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        raise PersistedStateError(
            "Persisted job state is unreadable or invalid.",
            details={"job_id": expected_id, "filename": job_json_path.name},
        ) from exc
    if record.id != expected_id:
        raise PersistedStateError(
            "Persisted job state has a mismatched identifier.",
            details={"job_id": expected_id, "stored_job_id": record.id},
        )
    return record


def read_job(settings: WebSettings, job_id: str) -> JobRecord:
    safe_id = validate_job_id(job_id)
    job_json_path = job_dir_for_id(settings, safe_id) / "job.json"
    if not job_json_path.is_file():
        raise FileNotFoundError(job_json_path)
    return _decode_job_record(job_json_path, expected_id=safe_id)


def list_jobs(settings: WebSettings) -> list[JobRecord]:
    records: list[JobRecord] = []
    if not settings.jobs_dir.exists():
        return records

    for child in sorted(settings.jobs_dir.iterdir(), key=lambda path: path.name):
        if not child.is_dir():
            continue
        safe_id = validate_job_id(child.name)
        job_json_path = child / "job.json"
        if not job_json_path.is_file():
            raise PersistedStateError(
                "A job workspace is missing its canonical state file.",
                details={"job_id": safe_id, "filename": "job.json"},
            )
        records.append(_decode_job_record(job_json_path, expected_id=safe_id))

    return sorted(records, key=lambda record: (record.created_at, record.id), reverse=True)


def update_job_status(
    settings: WebSettings,
    record: JobRecord,
    *,
    status: JobStatus,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> JobRecord:
    updated = replace(
        record,
        status=status,
        updated_at=_utc_now(),
        result=result if result is not None else record.result,
        error=error if error is not None else record.error,
    )
    write_job(settings, updated)
    return updated


def reconcile_interrupted_jobs(settings: WebSettings) -> list[str]:
    """Fail synchronous jobs left nonterminal by a previous process."""

    reconciled: list[str] = []
    for record in list_jobs(settings):
        if record.status not in {"queued", "running"}:
            continue
        update_job_status(
            settings,
            record,
            status="failed",
            result=record.result,
            error={
                "type": "interrupted_error",
                "code": "JOB_INTERRUPTED_BY_RESTART",
                "message": "Job execution was interrupted by a service restart.",
                "details": {"job_id": record.id},
            },
        )
        reconciled.append(record.id)
        LOGGER.warning("reconciled interrupted job", extra={"job_id": record.id})
    return reconciled
