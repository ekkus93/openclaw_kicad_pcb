"""Artifact listing and download helpers."""

from __future__ import annotations

import logging
import os
import tempfile
import zipfile
from pathlib import Path

from kicad_pcb.errors import ErrorCode, UserError

from ..errors import PersistenceError

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
    """Atomically publish a contained, symlink-free project as ``artifacts/project.zip``."""

    archive_members = _project_archive_members(project_dir)
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
                for path in archive_members:
                    archive.write(path, arcname=path.relative_to(project_dir.parent))
            handle.flush()
            os.fsync(handle.fileno())
        if temp_path is None:
            raise RuntimeError("project archive temporary path was not initialized")
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


def _project_archive_members(project_dir: Path) -> tuple[Path, ...]:
    if project_dir.is_symlink() or not project_dir.is_dir():
        raise PersistenceError(
            "Generated project archive source is missing or unsafe.",
            code="PROJECT_ARCHIVE_UNSAFE_PATH",
        )

    project_root = project_dir.resolve()
    members: list[Path] = []
    for path in sorted(project_dir.rglob("*")):
        relative = path.relative_to(project_dir)
        if path.is_symlink():
            raise PersistenceError(
                "Generated project contains a symbolic link and cannot be archived safely.",
                code="PROJECT_ARCHIVE_UNSAFE_PATH",
                details={"entry": str(relative)},
            )
        resolved = path.resolve()
        try:
            resolved.relative_to(project_root)
        except ValueError as exc:
            raise PersistenceError(
                "Generated project archive entry escaped the project root.",
                code="PROJECT_ARCHIVE_UNSAFE_PATH",
                details={"entry": str(relative)},
            ) from exc
        if path.is_file():
            members.append(path)
    return tuple(members)
