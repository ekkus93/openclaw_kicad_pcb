from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from kicad_pcb_web.errors import PersistenceError
from kicad_pcb_web.services import artifacts


def _staging_files(artifacts_dir: Path) -> list[Path]:
    return list((artifacts_dir / ".staging").glob("*"))


def test_project_zip_failure_preserves_previous_complete_archive(
    monkeypatch, tmp_path: Path
) -> None:
    project_dir = tmp_path / "project" / "Demo"
    artifacts_dir = tmp_path / "artifacts"
    project_dir.mkdir(parents=True)
    artifacts_dir.mkdir(parents=True)
    (project_dir / "Demo.kicad_sch").write_text("new schematic", encoding="utf-8")
    zip_path = artifacts_dir / "project.zip"
    zip_path.write_bytes(b"previous-complete-archive")

    def fail_write(self, filename, arcname=None, compress_type=None, compresslevel=None):
        raise OSError("simulated archive write failure")

    monkeypatch.setattr(zipfile.ZipFile, "write", fail_write)

    with pytest.raises(OSError, match="simulated archive write failure"):
        artifacts.create_project_zip(project_dir, artifacts_dir)

    assert zip_path.read_bytes() == b"previous-complete-archive"
    assert _staging_files(artifacts_dir) == []
    assert artifacts.list_artifacts(tmp_path) == ["project.zip"]


def test_project_zip_success_replaces_previous_archive(tmp_path: Path) -> None:
    project_dir = tmp_path / "project" / "Demo"
    artifacts_dir = tmp_path / "artifacts"
    project_dir.mkdir(parents=True)
    artifacts_dir.mkdir(parents=True)
    schematic = project_dir / "Demo.kicad_sch"
    schematic.write_text("new schematic", encoding="utf-8")
    zip_path = artifacts_dir / "project.zip"
    zip_path.write_bytes(b"previous-archive")

    published = artifacts.create_project_zip(project_dir, artifacts_dir)

    assert published == zip_path
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.read("Demo/Demo.kicad_sch") == b"new schematic"
    assert _staging_files(artifacts_dir) == []
    assert artifacts.list_artifacts(tmp_path) == ["project.zip"]


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation may require privileges")
def test_project_zip_rejects_symlink_without_replacing_previous_archive(tmp_path: Path) -> None:
    project_dir = tmp_path / "project" / "Demo"
    artifacts_dir = tmp_path / "artifacts"
    project_dir.mkdir(parents=True)
    artifacts_dir.mkdir(parents=True)
    outside = tmp_path / "private.txt"
    outside.write_text("private-data", encoding="utf-8")
    (project_dir / "leak.txt").symlink_to(outside)
    zip_path = artifacts_dir / "project.zip"
    zip_path.write_bytes(b"previous-complete-archive")

    with pytest.raises(PersistenceError, match="symbolic link") as exc_info:
        artifacts.create_project_zip(project_dir, artifacts_dir)

    assert exc_info.value.code == "PROJECT_ARCHIVE_UNSAFE_PATH"
    assert zip_path.read_bytes() == b"previous-complete-archive"
    assert not (artifacts_dir / ".staging").exists()
    assert artifacts.list_artifacts(tmp_path) == ["project.zip"]
