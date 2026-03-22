"""Tests for typed result objects (results.py) and formatting layer (formatting.py).

Phase 2.4: command handlers return structured results; the CLI layer formats and prints them.
These tests verify:
- Result dataclass construction and field access.
- format_result dispatches to the correct formatter and returns non-empty lines.
- Key content appears in formatted output (smoke-level, not string-exact).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kicad_pcb.formatting import format_result
from kicad_pcb.models import FootprintMoveSpec, LintIssue, ProjectRef, ValidationResult
from kicad_pcb.results import (
    AddComponentResult,
    AddNetResult,
    ApplyNetlistResult,
    AutoPlaceResult,
    AutoRouteResult,
    ConnectResult,
    DoctorCheckItem,
    DoctorResult,
    DrcResult,
    ErcResult,
    Export3dResult,
    ExportBomResult,
    ExportDrillResult,
    ExportGerbersResult,
    ExportPosResult,
    ImportNetlistResult,
    InfoResult,
    NewFromNetlistResult,
    NewProjectResult,
    OpenResult,
    PackageFabResult,
    PcbwayQuoteResult,
    PreviewPcbResult,
    PreviewSchematicResult,
    SetBoardSizeResult,
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
            warnings=({"code": "WARN", "message": "example"},),
            warning_report_path=tmp_path / "OpenClaw_Warnings.json",
            debug_dump_path=tmp_path / "OpenClaw_Debug.json",
        )
        text = joined(r)
        assert "OpenClaw_Warnings.json" in text
        assert "OpenClaw_Debug.json" in text


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
            warnings=({"code": "WARN", "message": "example"},),
            warning_report_path=tmp_path / "OpenClaw_Warnings.json",
            debug_dump_path=tmp_path / "OpenClaw_Debug.json",
        )
        text = joined(r)
        assert "OpenClaw_Warnings.json" in text
        assert "OpenClaw_Debug.json" in text


# ---------------------------------------------------------------------------
# Preview results
# ---------------------------------------------------------------------------


class TestPreviewSchematicResult:
    def test_format_with_png(self, tmp_path: Path) -> None:
        r = PreviewSchematicResult(
            svg_file=tmp_path / "sch.svg",
            png_file=tmp_path / "sch.png",
        )
        text = joined(r)
        assert "sch.svg" in text
        assert "sch.png" in text

    def test_format_without_png_shows_hint(self, tmp_path: Path) -> None:
        r = PreviewSchematicResult(svg_file=tmp_path / "sch.svg", png_file=None)
        text = joined(r)
        assert "cairosvg" in text


class TestPreviewPcbResult:
    def test_format_layer_files(self, tmp_path: Path) -> None:
        r = PreviewPcbResult(
            layer_files=(("F.Cu", tmp_path / "fcu.svg"),),
            glb_file=tmp_path / "board.glb",
        )
        text = joined(r)
        assert "F.Cu" in text
        assert "board.glb" in text

    def test_empty(self) -> None:
        r = PreviewPcbResult()
        assert format_result(r) == []


# ---------------------------------------------------------------------------
# PCB results
# ---------------------------------------------------------------------------


class TestSetBoardSizeResult:
    def test_format(self) -> None:
        r = SetBoardSizeResult(width=50.0, height=30.0, pcb_file_name="board.kicad_pcb")
        text = joined(r)
        assert "50" in text
        assert "30" in text
        assert "board.kicad_pcb" in text


class TestImportNetlistResult:
    def test_format_with_components(self, tmp_path: Path) -> None:
        r = ImportNetlistResult(
            netlist_file=tmp_path / "board.net",
            components=(
                ("R1", "10k", "Resistor_SMD:R_0402"),
                ("C1", "100nF", ""),
            ),
        )
        text = joined(r)
        assert "R1" in text
        assert "C1" in text
        assert "NO FOOTPRINT" in text
        assert "C1" in text  # in missing list

    def test_format_empty_components(self, tmp_path: Path) -> None:
        r = ImportNetlistResult(netlist_file=tmp_path / "board.net", components=())
        text = joined(r)
        assert "board.net" in text
        assert "Update PCB" in text


class TestAutoPlaceResult:
    def test_format(self) -> None:
        placed = (FootprintMoveSpec(ref="R1", x=10.0, y=20.0),)
        r = AutoPlaceResult(placed=placed, spacing=10.0)
        text = joined(r)
        assert "1" in text
        assert "R1" in text
        assert "10.0" in text

    def test_format_no_footprints_is_empty(self) -> None:
        r = AutoPlaceResult(placed=(), spacing=5.0)
        text = joined(r)
        assert "0" in text


class TestAutoRouteResult:
    def test_routes_imported(self) -> None:
        r = AutoRouteResult(ses_file_name="board.ses", routes_imported=True)
        text = joined(r)
        assert "board.ses" in text
        assert "imported" in text.lower()

    def test_routes_not_imported(self) -> None:
        r = AutoRouteResult(ses_file_name="board.ses", routes_imported=False)
        assert "Manual import" in joined(r)


# ---------------------------------------------------------------------------
# Schematic results
# ---------------------------------------------------------------------------


class TestAddComponentResult:
    def test_format_with_footprint(self) -> None:
        r = AddComponentResult(
            ref="R1",
            lib_sym="Device:R",
            value="10k",
            x=50.0,
            y=50.0,
            pins=("1", "2"),
            has_footprint=True,
        )
        text = joined(r)
        assert "R1" in text
        assert "Device:R" in text
        assert "10k" in text
        assert "footprint" not in text.lower()  # no warning

    def test_format_no_footprint_shows_warning(self) -> None:
        r = AddComponentResult(
            ref="U1",
            lib_sym="Device:MCU",
            value="",
            x=0.0,
            y=0.0,
            pins=("1",),
            has_footprint=False,
        )
        assert "footprint" in joined(r).lower()


class TestAddNetResult:
    def test_format(self) -> None:
        r = AddNetResult(name="VCC", x=60.0, y=50.0)
        text = joined(r)
        assert "VCC" in text
        assert "60.0" in text

    def test_frozen(self) -> None:
        r = AddNetResult(name="GND", x=0.0, y=0.0)
        with pytest.raises(AttributeError):
            r.name = "VDD"  # type: ignore[misc]


class TestConnectResult:
    def test_format(self) -> None:
        r = ConnectResult(x1=10.0, y1=20.0, x2=30.0, y2=20.0)
        text = joined(r)
        assert "10.0" in text
        assert "30.0" in text


# ---------------------------------------------------------------------------
# DoctorResult
# ---------------------------------------------------------------------------


class TestDoctorResult:
    def _make_checks(self) -> tuple[DoctorCheckItem, ...]:
        return (
            DoctorCheckItem(
                status="ok",
                label="kicad-cli",
                message="/usr/bin/kicad-cli",
                detail="version: 9.0.7",
            ),
            DoctorCheckItem(status="error", label="Symbol libraries", message="not found"),
            DoctorCheckItem(status="info", label="java", message="not found"),
        )

    def test_fields(self) -> None:
        r = DoctorResult(overall_ok=False, checks=self._make_checks())
        assert r.overall_ok is False
        assert len(r.checks) == 3

    def test_format_shows_all_labels(self) -> None:
        r = DoctorResult(overall_ok=False, checks=self._make_checks())
        text = joined(r)
        assert "kicad-cli" in text
        assert "Symbol libraries" in text
        assert "java" in text

    def test_format_shows_detail(self) -> None:
        r = DoctorResult(overall_ok=True, checks=self._make_checks())
        assert "version: 9.0.7" in joined(r)

    def test_format_ok_shows_all_passed(self) -> None:
        r = DoctorResult(overall_ok=True, checks=())
        assert "All checks passed" in joined(r)

    def test_format_fail_does_not_show_all_passed(self) -> None:
        r = DoctorResult(overall_ok=False, checks=())
        assert "All checks passed" not in joined(r)

    def test_status_icons(self) -> None:
        r = DoctorResult(overall_ok=False, checks=self._make_checks())
        text = joined(r)
        assert "✅" in text  # ok item
        assert "❌" in text  # error item
        assert "ℹ️" in text  # info item


# ---------------------------------------------------------------------------
# PcbwayQuoteResult
# ---------------------------------------------------------------------------


class TestPcbwayQuoteResult:
    def test_fields(self) -> None:
        r = PcbwayQuoteResult(
            quantity=5,
            layers=2,
            thickness=1.6,
            board_cost=5.0,
            shipping=18.0,
            total=23.0,
        )
        assert r.total == 23.0
        assert r.gerber_zip is None

    def test_format_shows_quantities_and_total(self) -> None:
        r = PcbwayQuoteResult(
            quantity=10,
            layers=4,
            thickness=1.6,
            board_cost=10.0,
            shipping=18.0,
            total=28.0,
        )
        text = joined(r)
        assert "10" in text  # quantity
        assert "4" in text  # layers
        assert "28.00" in text  # total

    def test_format_with_gerber_zip(self, tmp_path: Path) -> None:
        r = PcbwayQuoteResult(
            quantity=5,
            layers=2,
            thickness=1.6,
            board_cost=5.0,
            shipping=18.0,
            total=23.0,
            gerber_zip=tmp_path / "fab.zip",
        )
        assert "fab.zip" in joined(r)

    def test_format_without_gerber_zip_shows_hint(self) -> None:
        r = PcbwayQuoteResult(
            quantity=5,
            layers=2,
            thickness=1.6,
            board_cost=5.0,
            shipping=18.0,
            total=23.0,
        )
        assert "package-for-fab" in joined(r)


# ---------------------------------------------------------------------------
# format_result dispatch
# ---------------------------------------------------------------------------


class TestFormatResultDispatch:
    def test_unknown_type_returns_repr(self) -> None:
        lines = format_result("not a result")
        assert len(lines) == 1
        assert "not a result" in lines[0]

    def test_returns_list_of_str(self, tmp_path: Path) -> None:
        r = OpenResult(name="x", path=tmp_path)
        lines = format_result(r)
        assert isinstance(lines, list)
        assert all(isinstance(line, str) for line in lines)

    def test_all_result_types_are_registered(self, tmp_path: Path) -> None:
        """Smoke-check every result type returns non-empty output."""
        proj = ProjectRef(name="p", path=tmp_path)
        v = ValidationResult(passed=True, issues=[])
        samples = [
            NewProjectResult(name="x", path=tmp_path, description=""),
            InfoResult(project=proj, files=()),
            OpenResult(name="x", path=tmp_path),
            DrcResult(passed=True, stderr=""),
            DrcResult(passed=True, stderr="", validation=v),
            ErcResult(passed=True, stderr=""),
            ExportGerbersResult(output_dir=tmp_path, files=()),
            ExportDrillResult(output_dir=tmp_path),
            ExportBomResult(output_file=tmp_path / "bom.csv", lines=("header",)),
            PackageFabResult(output_path=tmp_path / "fab.zip", size_bytes=1024),
            ExportPosResult(output_file=tmp_path / "pos.csv", component_count=0),
            Export3dResult(output_file=tmp_path / "board.step", size_bytes=1024),
            PreviewSchematicResult(svg_file=tmp_path / "sch.svg"),
            PreviewPcbResult(),
            SetBoardSizeResult(width=50.0, height=30.0, pcb_file_name="board.kicad_pcb"),
            ImportNetlistResult(netlist_file=tmp_path / "board.net"),
            AutoPlaceResult(placed=(), spacing=10.0),
            AutoRouteResult(ses_file_name="board.ses", routes_imported=True),
            AddComponentResult(
                ref="R1",
                lib_sym="Device:R",
                value="1k",
                x=0.0,
                y=0.0,
                pins=("1", "2"),
                has_footprint=True,
            ),
            AddNetResult(name="VCC", x=0.0, y=0.0),
            ConnectResult(x1=0.0, y1=0.0, x2=10.0, y2=0.0),
            DoctorResult(overall_ok=True, checks=()),
            PcbwayQuoteResult(
                quantity=5, layers=2, thickness=1.6, board_cost=5.0, shipping=18.0, total=23.0
            ),
        ]
        for sample in samples:
            lines = format_result(sample)
            assert isinstance(lines, list), (
                f"format_result({type(sample).__name__}) must return list"
            )
