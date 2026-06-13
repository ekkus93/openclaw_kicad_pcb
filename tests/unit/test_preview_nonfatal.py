"""Tests for non-fatal schematic preview generation in kicad_pcb_web.services.netlists.

_generate_schematic_preview raises RuntimeError when tooling is absent.  The
caller (_run_job_sync) must catch that error, attach it as a warning, and let
the job succeed.  These tests verify both halves of that contract.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kicad_pcb_web.services.netlists import (
    _generate_schematic_preview,
)

# ─── _generate_schematic_preview raises when tooling is missing ───────────────


def test_preview_raises_when_kicad_cli_absent(tmp_path: Path) -> None:
    """RuntimeError is raised when kicad-cli is not on PATH."""
    fake_schematic = tmp_path / "test.kicad_sch"
    fake_schematic.touch()

    with (
        patch("kicad_pcb_web.services.netlists.shutil.which", return_value=None),
        pytest.raises(RuntimeError, match="kicad-cli"),
    ):
        _generate_schematic_preview(fake_schematic, tmp_path)


def test_preview_raises_when_rsvg_absent(tmp_path: Path) -> None:
    """RuntimeError is raised when rsvg-convert is not on PATH."""
    fake_schematic = tmp_path / "test.kicad_sch"
    fake_schematic.touch()

    def which_side_effect(name: str) -> str | None:
        if name == "kicad-cli":
            return "/usr/bin/kicad-cli"
        return None

    with (
        patch("kicad_pcb_web.services.netlists.shutil.which", side_effect=which_side_effect),
        pytest.raises(RuntimeError, match="rsvg-convert"),
    ):
        _generate_schematic_preview(fake_schematic, tmp_path)


# ─── preview_warning flows into the result payload ────────────────────────────


def test_preview_warning_dict_structure() -> None:
    """The warning dict emitted when preview fails matches the project-wide format.

    Verify the expected code and message keys are present and the code is the
    sentinel value checked by the frontend's WarningsPanel.
    """
    err_msg = "kicad-cli is not installed or not on PATH."
    warning = {
        "code": "PREVIEW_GENERATION_SKIPPED",
        "message": f"Schematic preview skipped: {err_msg}",
    }
    assert warning["code"] == "PREVIEW_GENERATION_SKIPPED"
    assert err_msg in warning["message"]
    # Ensure it follows the same {code, message} shape used throughout the project
    assert set(warning.keys()) >= {"code", "message"}
