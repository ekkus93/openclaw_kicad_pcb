"""Tests for typed result objects (results.py) and formatting layer (formatting.py).

Phase 2.4: command handlers return structured results; the CLI layer formats and prints them.
These tests verify:
- Result dataclass construction and field access.
- format_result dispatches to the correct formatter and returns non-empty lines.
- Key content appears in formatted output (smoke-level, not string-exact).
"""

from __future__ import annotations

from pathlib import Path

from kicad_pcb.formatting import format_result
from kicad_pcb.models import LintIssue, ProjectRef, ValidationResult
from kicad_pcb.results import (
    ApplyNetlistResult,
    DrcResult,
    ErcResult,
    Export3dResult,
    ExportBomResult,
    ExportDrillResult,
    ExportGerbersResult,
    ExportPosResult,
    GeneratedSchematicDiagnostics,
    InfoResult,
    NewFromNetlistResult,
    NewProjectResult,
    OpenResult,
    PackageFabResult,
)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def joined(result: object) -> str:
    """Return format_result lines joined by newline for easy substring checks."""
    return "\n".join(format_result(result))


# ---------------------------------------------------------------------------
# NewProjectResult
# ---------------------------------------------------------------------------


class TestNewProjectResult:
    def test_fields(self, tmp_path: Path) -> None:
        r = NewProjectResult(
            name="demo",
            path=tmp_path,
            description="A demo project",
            files=("demo.kicad_pro", "demo.kicad_sch", "demo.kicad_pcb"),
        )
        assert r.name == "demo"
        assert r.path == tmp_path
        assert r.description == "A demo project"
        assert len(r.files) == 3

    def test_format_contains_name_and_files(self, tmp_path: Path) -> None:
        r = NewProjectResult(
            name="myboard",
            path=tmp_path,
            description="",
            files=("myboard.kicad_pro", "myboard.kicad_sch"),
        )
        text = joined(r)
        assert "myboard" in text
        assert "myboard.kicad_pro" in text
        assert "myboard.kicad_sch" in text

    def test_format_includes_description_when_set(self, tmp_path: Path) -> None:
        r = NewProjectResult(name="x", path=tmp_path, description="My desc")
        assert "My desc" in joined(r)

    def test_format_no_description_line_when_empty(self, tmp_path: Path) -> None:
        r = NewProjectResult(name="x", path=tmp_path, description="")
        assert "Description" not in joined(r)


# ---------------------------------------------------------------------------
# InfoResult
# ---------------------------------------------------------------------------


class TestInfoResult:
    def test_fields(self, tmp_path: Path) -> None:
        proj = ProjectRef(name="p", path=tmp_path)
        r = InfoResult(project=proj, files=(("p.kicad_pro", 1024),))
        assert r.project is proj
        assert r.files[0] == ("p.kicad_pro", 1024)

    def test_format_shows_project_name(self, tmp_path: Path) -> None:
        proj = ProjectRef(name="myproj", path=tmp_path)
        r = InfoResult(project=proj, files=())
        assert "myproj" in joined(r)

    def test_format_shows_files_with_sizes(self, tmp_path: Path) -> None:
        proj = ProjectRef(name="p", path=tmp_path)
        r = InfoResult(project=proj, files=(("foo.kicad_sch", 512),))
        text = joined(r)
        assert "foo.kicad_sch" in text
        assert "512" in text


# ---------------------------------------------------------------------------
# OpenResult
# ---------------------------------------------------------------------------


class TestOpenResult:
    def test_fields(self, tmp_path: Path) -> None:
        r = OpenResult(name="board", path=tmp_path)
        assert r.name == "board"
        assert r.path == tmp_path

    def test_format_contains_name_and_path(self, tmp_path: Path) -> None:
        r = OpenResult(name="board", path=tmp_path)
        text = joined(r)
        assert "board" in text
        assert str(tmp_path) in text


# ---------------------------------------------------------------------------
# DrcResult / ErcResult
# ---------------------------------------------------------------------------


class TestDrcResult:
    def test_passed_true(self) -> None:
        r = DrcResult(passed=True, stderr="")
        assert "passed" in joined(r).lower()

    def test_passed_false_shows_issues(self) -> None:
        r = DrcResult(passed=False, stderr="some error")
        text = joined(r)
        assert "issues" in text.lower() or "completed" in text.lower()
        assert "some error" in text

    def test_with_validation_shows_issue_count(self) -> None:
        issues = [LintIssue(severity="error", description="short circuit")]
        v = ValidationResult(passed=False, issues=issues)
        r = DrcResult(passed=False, stderr="", validation=v)
        assert "1" in joined(r)
        assert "short circuit" in joined(r)

    def test_with_clean_validation(self) -> None:
        v = ValidationResult(passed=True, issues=[])
        r = DrcResult(passed=True, stderr="", validation=v)
        assert "No violations" in joined(r)


class TestErcResult:
    def test_passed(self) -> None:
        assert "passed" in joined(ErcResult(passed=True, stderr="")).lower()

    def test_failed_with_stderr(self) -> None:
        text = joined(ErcResult(passed=False, stderr="pin unconnected"))
        assert "issues" in text.lower() or "completed" in text.lower()
        assert "pin unconnected" in text


# ---------------------------------------------------------------------------
# Export results
# ---------------------------------------------------------------------------


class TestExportGerbersResult:
    def test_fields_and_format(self, tmp_path: Path) -> None:
        f1, f2 = tmp_path / "a.gtl", tmp_path / "b.gbl"
        r = ExportGerbersResult(output_dir=tmp_path, files=(f1, f2))
        text = joined(r)
        assert "2" in text
        assert "a.gtl" in text

    def test_empty_files(self, tmp_path: Path) -> None:
        r = ExportGerbersResult(output_dir=tmp_path, files=())
        assert "0" in joined(r)


class TestExportDrillResult:
    def test_format(self, tmp_path: Path) -> None:
        r = ExportDrillResult(output_dir=tmp_path)
        assert str(tmp_path) in joined(r)


class TestExportBomResult:
    def test_format_shows_line_count(self, tmp_path: Path) -> None:
        r = ExportBomResult(
            output_file=tmp_path / "bom.csv",
            lines=("header", "R1,R,100"),
        )
        text = joined(r)
        assert "bom.csv" in text
        assert "1" in text  # max(0, 2-1) = 1 component line

    def test_format_shows_lines(self, tmp_path: Path) -> None:
        r = ExportBomResult(
            output_file=tmp_path / "bom.csv",
            lines=("header", "R1,R,100"),
        )
        assert "R1,R,100" in joined(r)


class TestPackageFabResult:
    def test_format(self, tmp_path: Path) -> None:
        out = tmp_path / "fab.zip"
        r = PackageFabResult(output_path=out, size_bytes=2048)
        text = joined(r)
        assert "fab.zip" in text
        assert "2.0" in text  # 2048 / 1024 = 2.0 KB


class TestExportPosResult:
    def test_format(self, tmp_path: Path) -> None:
        r = ExportPosResult(output_file=tmp_path / "pos.csv", component_count=5)
        text = joined(r)
        assert "pos.csv" in text
        assert "5" in text


class TestExport3dResult:
    def test_format(self, tmp_path: Path) -> None:
        r = Export3dResult(output_file=tmp_path / "board.step", size_bytes=10240)
        text = joined(r)
        assert "board.step" in text
        assert "10.0" in text  # KB


class TestApplyNetlistResult:
    def test_format_shows_warning_report_path(self, tmp_path: Path) -> None:
        r = ApplyNetlistResult(
            schematic_path=tmp_path / "root.kicad_sch",
            managed_schematic_path=tmp_path / "OpenClaw_Managed.kicad_sch",
            symbols_added=2,
            symbols_updated=0,
            managed_items_written=5,
            nets_applied=3,
            kicad_cli_used=False,
            heuristic_profile_name="generic_digital",
            label_mode_name="debug",
            warnings=({"code": "WARN", "message": "example"},),
            warning_report_path=tmp_path / "OpenClaw_Warnings.json",
            debug_dump_path=tmp_path / "OpenClaw_Debug.json",
            generated_schematic_diagnostics=GeneratedSchematicDiagnostics(
                symbol_count=2,
                wire_count=3,
                label_count=1,
                junction_count=0,
            ),
        )
        text = joined(r)
        assert "OpenClaw_Warnings.json" in text
        assert "OpenClaw_Debug.json" in text
        assert "generic_digital" in text
        assert "debug" in text
        assert "Generated structure" in text


class TestNewFromNetlistResult:
    def test_format_shows_warning_report_path(self, tmp_path: Path) -> None:
        r = NewFromNetlistResult(
            name="demo",
            path=tmp_path,
            schematic_path=tmp_path / "demo.kicad_sch",
            managed_schematic_path=tmp_path / "OpenClaw_Managed.kicad_sch",
            symbols_added=2,
            nets_applied=3,
            kicad_cli_used=False,
            heuristic_profile_name="power_supply",
            label_mode_name="always-show-important-labels",
            warnings=({"code": "WARN", "message": "example"},),
            warning_report_path=tmp_path / "OpenClaw_Warnings.json",
            debug_dump_path=tmp_path / "OpenClaw_Debug.json",
            generated_schematic_diagnostics=GeneratedSchematicDiagnostics(
                symbol_count=2,
                wire_count=3,
                label_count=1,
                junction_count=0,
            ),
        )
        text = joined(r)
        assert "OpenClaw_Warnings.json" in text
        assert "OpenClaw_Debug.json" in text
        assert "power_supply" in text
        assert "always-show-important-labels" in text
        assert "Generated structure" in text


# ---------------------------------------------------------------------------
# Preview results
# ---------------------------------------------------------------------------
