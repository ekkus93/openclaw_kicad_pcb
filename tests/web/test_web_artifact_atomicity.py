from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from kicad_pcb_web.services import artifacts


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
    assert not list(artifacts_dir.glob(".project.*.zip.tmp"))


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
    assert not list(artifacts_dir.glob(".project.*.zip.tmp"))
