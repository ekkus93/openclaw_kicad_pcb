"""Artifact listing and download helpers."""

from __future__ import annotations

import logging
import os
import tempfile
import zipfile
from pathlib import Path

from kicad_pcb.errors import ErrorCode, UserError

LOGGER = logging.getLogger("uvicorn.error")
_UNSAFE_ARTIFACT_PARTS = ("..", "/", "\\")
_PRIVATE_ARTIFACT_NAMES = frozenset({"job.json"})


def list_artifacts(job_dir: Path) -> list[str]:
    """Return artifact filenames for one job."""

    artifacts_dir = job_dir / "artifacts"
    if not artifacts_dir.is_dir():
        return []
    return sorted(
        path.name
        for path in artifacts_dir.iterdir()
        if path.is_file() and path.name not in _PRIVATE_ARTIFACT_NAMES
    )


def resolve_artifact_path(job_dir: Path, artifact_name: str) -> Path:
    """Resolve a safe artifact path under ``job_dir/artifacts``."""

    if artifact_name in _PRIVATE_ARTIFACT_NAMES:
        raise FileNotFoundError(artifact_name)
    if not artifact_name or any(part in artifact_name for part in _UNSAFE_ARTIFACT_PARTS):
        raise UserError(
            f"Unsafe artifact name: {artifact_name!r}",
            code=ErrorCode.USER_ERROR,
            details={"artifact_name": artifact_name},
        )

    artifacts_dir = (job_dir / "artifacts").resolve()
    candidate = (artifacts_dir / artifact_name).resolve()
    try:
        candidate.relative_to(artifacts_dir)
    except ValueError as exc:
        raise UserError(
            f"Unsafe artifact path: {artifact_name!r}",
            code=ErrorCode.USER_ERROR,
            details={"artifact_name": artifact_name},
        ) from exc

    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def create_project_zip(project_dir: Path, artifacts_dir: Path) -> Path:
    """Atomically publish the generated project tree as ``artifacts/project.zip``."""

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    zip_path = artifacts_dir / "project.zip"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=artifacts_dir,
            prefix=".project.",
            suffix=".zip.tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(project_dir.rglob("*")):
                    if path.is_file():
                        archive.write(path, arcname=path.relative_to(project_dir.parent))
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(zip_path)
        temp_path = None
        return zip_path
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as exc:
                LOGGER.warning(
                    "failed to remove temporary project archive",
                    extra={"artifact_name": temp_path.name, "error_type": type(exc).__name__},
                )
