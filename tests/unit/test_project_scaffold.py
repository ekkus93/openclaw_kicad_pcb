"""Tests for project scaffolding helpers used by CLI and web paths."""

from __future__ import annotations

from pathlib import Path

from kicad_pcb.commands._project import _create_project, create_project_files


def test_create_project_files_creates_seed_kicad_files(tmp_path: Path) -> None:
    project = create_project_files(name="Web Demo", out_dir=tmp_path, description="demo")

    assert project.path == tmp_path / "Web_Demo"
    assert project.pro_file.exists()
    assert project.sch_file.exists()
    assert project.pcb_file.exists()


def test_create_project_files_does_not_write_current_project_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    current_project_file = tmp_path / "state" / "current_project.json"
    current_project_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("kicad_pcb.config.CURRENT_PROJECT_FILE", current_project_file)

    create_project_files(name="No State", out_dir=tmp_path, description="")

    assert not current_project_file.exists()


def test__create_project_keeps_cli_current_project_behavior(
    tmp_path: Path,
    monkeypatch,
) -> None:
    current_project_file = tmp_path / "state" / "current_project.json"
    current_project_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("kicad_pcb.config.CURRENT_PROJECT_FILE", current_project_file)

    project = _create_project(name="CLI Demo", out_dir=tmp_path, description="")

    assert project.path == tmp_path / "CLI_Demo"
    assert current_project_file.exists()
