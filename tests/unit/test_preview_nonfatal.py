"""Tests for explicit non-fatal schematic preview degradation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kicad_pcb.results import ApplyNetlistResult
from kicad_pcb_web.schemas import CreateJobFromNetlistRequest
from kicad_pcb_web.services.netlists import (
    _generate_schematic_preview,
    generate_project_from_netlist_job,
)
from kicad_pcb_web.settings import LlmSettings, WebSettings

_VALID_NETLIST = {
    "version": "1",
    "components": [{"ref": "R1", "symbol": "Device:R", "value": "10k"}],
    "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "1"}]}],
}


def _settings(tmp_path: Path) -> WebSettings:
    data_dir = tmp_path / "data"
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True)
    return WebSettings(data_dir=data_dir, jobs_dir=jobs_dir, llm=LlmSettings())


def test_preview_raises_when_kicad_cli_absent(tmp_path: Path) -> None:
    fake_schematic = tmp_path / "test.kicad_sch"
    fake_schematic.touch()

    with (
        patch("kicad_pcb_web.services.netlists.shutil.which", return_value=None),
        pytest.raises(RuntimeError, match="kicad-cli"),
    ):
        _generate_schematic_preview(fake_schematic, tmp_path)


def test_preview_raises_when_rsvg_absent(tmp_path: Path) -> None:
    fake_schematic = tmp_path / "test.kicad_sch"
    fake_schematic.touch()

    def which_side_effect(name: str) -> str | None:
        return "/usr/bin/kicad-cli" if name == "kicad-cli" else None

    with (
        patch(
            "kicad_pcb_web.services.netlists.shutil.which",
            side_effect=which_side_effect,
        ),
        pytest.raises(RuntimeError, match="rsvg-convert"),
    ):
        _generate_schematic_preview(fake_schematic, tmp_path)


def test_preview_failure_keeps_valid_project_job_successful(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    symbol_index = SimpleNamespace(directories=())
    ir = SimpleNamespace(components=[object()], nets=[object()])

    def fake_apply(project, _request) -> ApplyNetlistResult:
        managed = project.path / "OpenClaw_Managed.kicad_sch"
        managed.write_text("(kicad_sch)", encoding="utf-8")
        return ApplyNetlistResult(
            schematic_path=project.sch_file,
            managed_schematic_path=managed,
            symbols_added=1,
            symbols_updated=0,
            managed_items_written=1,
            nets_applied=1,
            kicad_cli_used=False,
            heuristic_profile_name="generic_digital",
            label_mode_name="auto",
        )

    with (
        patch(
            "kicad_pcb_web.services.netlists._validate_with_optional_autofix",
            return_value=(tmp_path / "effective.json", symbol_index, ir, []),
        ),
        patch(
            "kicad_pcb_web.services.netlists._apply_netlist_to_project",
            side_effect=fake_apply,
        ),
        patch(
            "kicad_pcb_web.services.netlists._generate_schematic_preview",
            side_effect=RuntimeError("preview dependency missing"),
        ),
    ):
        job = generate_project_from_netlist_job(
            settings=settings,
            request=CreateJobFromNetlistRequest(
                project_name="PreviewDegraded",
                netlist_json=_VALID_NETLIST,
                validation="internal",
            ),
        )

    assert job.status == "succeeded"
    assert "project.zip" in job.artifacts
    assert "schematic_preview.png" not in job.artifacts
    assert job.result is not None
    warnings = job.result["warnings"]
    assert warnings[0]["code"] == "PREVIEW_GENERATION_SKIPPED"
    assert "preview dependency missing" in warnings[0]["message"]


def test_non_preview_generation_failure_still_fails_job(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    symbol_index = SimpleNamespace(directories=())
    ir = SimpleNamespace(components=[object()], nets=[object()])

    with (
        patch(
            "kicad_pcb_web.services.netlists._validate_with_optional_autofix",
            return_value=(tmp_path / "effective.json", symbol_index, ir, []),
        ),
        patch(
            "kicad_pcb_web.services.netlists._apply_netlist_to_project",
            side_effect=RuntimeError("generation exploded"),
        ),
    ):
        job = generate_project_from_netlist_job(
            settings=settings,
            request=CreateJobFromNetlistRequest(
                project_name="BrokenGeneration",
                netlist_json=_VALID_NETLIST,
                validation="internal",
            ),
        )

    assert job.status == "failed"
    assert "project.zip" not in job.artifacts
    assert job.error is not None
    assert job.error["code"] == "INTERNAL_SERVER_ERROR"
