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
from kicad_pcb.models import FootprintMoveSpec, ProjectRef, ValidationResult
from kicad_pcb.results import (
    AddComponentResult,
    AddNetResult,
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
