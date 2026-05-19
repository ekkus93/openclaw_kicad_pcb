"""Unit tests for kicad_pcb.models — typed domain objects."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb.models import (
    BoardOutlineRect,
    ComponentSpec,
    FootprintMoveSpec,
    LintIssue,
    NetLabelSpec,
    ProjectRef,
    ValidationResult,
    WireSegment,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ns(**kwargs) -> Namespace:
    """Build a minimal argparse namespace from keyword arguments."""
    return Namespace(**kwargs)


# ---------------------------------------------------------------------------
# ProjectRef
# ---------------------------------------------------------------------------


class TestProjectRef:
    def test_from_dict_round_trip(self) -> None:
        d = {
            "name": "my_board",
            "path": "/tmp/kicad/my_board",
            "created": "2026-01-01T00:00:00",
            "description": "test board",
        }
        ref = ProjectRef.from_dict(d)
        assert ref.name == "my_board"
        assert ref.path == Path("/tmp/kicad/my_board")
        assert ref.created == "2026-01-01T00:00:00"
        assert ref.description == "test board"
        # round-trip
        assert ref.to_dict() == d

    def test_from_dict_optional_fields_default(self) -> None:
        ref = ProjectRef.from_dict({"name": "x", "path": "/tmp/x"})
        assert ref.created == ""
        assert ref.description == ""

    def test_from_dict_opened_key_compat(self) -> None:
        """Legacy dicts stored 'opened' instead of 'created' for cmd_open."""
        ref = ProjectRef.from_dict({"name": "x", "path": "/tmp/x", "opened": "2026-02-01T09:00:00"})
        assert ref.created == "2026-02-01T09:00:00"

    def test_file_path_properties(self, tmp_path: Path) -> None:
        ref = ProjectRef(name="demo", path=tmp_path)
        assert ref.sch_file == tmp_path / "demo.kicad_sch"
        assert ref.pcb_file == tmp_path / "demo.kicad_pcb"
        assert ref.pro_file == tmp_path / "demo.kicad_pro"

    def test_to_dict_serialises_path_as_string(self) -> None:
        ref = ProjectRef(name="demo", path=Path("/some/dir"))
        d = ref.to_dict()
        assert isinstance(d["path"], str)
        assert d["path"] == "/some/dir"

    def test_frozen(self) -> None:
        ref = ProjectRef(name="x", path=Path("/tmp"))
        with pytest.raises((AttributeError, TypeError)):
            ref.name = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ComponentSpec
# ---------------------------------------------------------------------------


class TestComponentSpec:
    def test_from_args_basic(self) -> None:
        args = _ns(lib_sym="Device:R", ref="R1", value="10k", footprint="Resistor_SMD:R_0402")
        spec = ComponentSpec.from_args(args)
        assert spec.lib_sym == "Device:R"
        assert spec.lib_name == "Device"
        assert spec.sym_name == "R"
        assert spec.ref == "R1"
        assert spec.value == "10k"
        assert spec.footprint == "Resistor_SMD:R_0402"

    def test_from_args_value_defaults_to_sym_name(self) -> None:
        args = _ns(lib_sym="Device:R", ref="R1", value=None, footprint=None)
        spec = ComponentSpec.from_args(args)
        assert spec.value == "R"

    def test_from_args_no_colon_raises(self) -> None:
        args = _ns(lib_sym="DeviceR", ref="R1", value=None, footprint=None)
        with pytest.raises(UserError, match="Library:Symbol"):
            ComponentSpec.from_args(args)

    def test_from_args_empty_footprint(self) -> None:
        args = _ns(lib_sym="Device:C", ref="C1", value="100n", footprint=None)
        spec = ComponentSpec.from_args(args)
        assert spec.footprint == ""

    def test_lib_name_sym_name_properties(self) -> None:
        spec = ComponentSpec(lib_sym="Power:VCC", ref="PWR1", value="VCC")
        assert spec.lib_name == "Power"
        assert spec.sym_name == "VCC"


# ---------------------------------------------------------------------------
# WireSegment
# ---------------------------------------------------------------------------


class TestWireSegment:
    def test_from_args(self) -> None:
        args = _ns(from_pt="50.8,76.2", to_pt="76.2,76.2")
        wire = WireSegment.from_args(args)
        assert wire.x1 == pytest.approx(50.8)
        assert wire.y1 == pytest.approx(76.2)
        assert wire.x2 == pytest.approx(76.2)
        assert wire.y2 == pytest.approx(76.2)

    def test_from_args_bad_format_raises(self) -> None:
        args = _ns(from_pt="notacoord", to_pt="76,76")
        with pytest.raises(UserError, match="Coordinates"):
            WireSegment.from_args(args)

    def test_from_args_missing_pair_raises(self) -> None:
        args = _ns(from_pt="10", to_pt="20,30")
        with pytest.raises(UserError):
            WireSegment.from_args(args)


# ---------------------------------------------------------------------------
# NetLabelSpec
# ---------------------------------------------------------------------------


class TestNetLabelSpec:
    def test_from_args_with_coordinates(self) -> None:
        args = _ns(name="VCC", x=60.0, y=50.0)
        label = NetLabelSpec.from_args(args)
        assert label.name == "VCC"
        assert label.x == pytest.approx(60.0)
        assert label.y == pytest.approx(50.0)

    def test_from_args_defaults_coordinates(self) -> None:
        args = _ns(name="GND", x=None, y=None)
        label = NetLabelSpec.from_args(args)
        assert label.x == pytest.approx(50.8)
        assert label.y == pytest.approx(50.8)


# ---------------------------------------------------------------------------
# BoardOutlineRect
# ---------------------------------------------------------------------------


class TestBoardOutlineRect:
    def test_from_args_basic(self) -> None:
        args = _ns(size="50x30")
        rect = BoardOutlineRect.from_args(args)
        assert rect.width == pytest.approx(50.0)
        assert rect.height == pytest.approx(30.0)

    def test_from_args_case_insensitive(self) -> None:
        args = _ns(size="100X80")
        rect = BoardOutlineRect.from_args(args)
        assert rect.width == pytest.approx(100.0)
        assert rect.height == pytest.approx(80.0)

    def test_from_args_bad_format_raises(self) -> None:
        args = _ns(size="fifty by thirty")
        with pytest.raises(UserError, match="WxH"):
            BoardOutlineRect.from_args(args)

    def test_from_args_zero_dimension_raises(self) -> None:
        args = _ns(size="0x30")
        with pytest.raises(UserError, match="positive"):
            BoardOutlineRect.from_args(args)

    def test_corners_form_closed_rectangle(self) -> None:
        rect = BoardOutlineRect(width=50.0, height=30.0)
        corners = rect.corners
        assert len(corners) == 4
        # Each segment: start == previous end
        for i, (start, end) in enumerate(corners):
            prev_end = corners[i - 1][1]
            assert start == pytest.approx(prev_end), f"Gap at segment {i}"
        # First start at origin
        assert corners[0][0] == pytest.approx((0.0, 0.0))
        # Last end back at origin
        assert corners[-1][1] == pytest.approx((0.0, 0.0))


# ---------------------------------------------------------------------------
# FootprintMoveSpec
# ---------------------------------------------------------------------------


class TestFootprintMoveSpec:
    def test_construction(self) -> None:
        spec = FootprintMoveSpec(ref="U1", x=10.5, y=22.3)
        assert spec.ref == "U1"
        assert spec.x == pytest.approx(10.5)
        assert spec.y == pytest.approx(22.3)

    def test_frozen(self) -> None:
        spec = FootprintMoveSpec(ref="U1", x=0.0, y=0.0)
        with pytest.raises((AttributeError, TypeError)):
            spec.x = 1.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# LintIssue
# ---------------------------------------------------------------------------


class TestLintIssue:
    def test_from_dict_full(self) -> None:
        d = {"severity": "error", "description": "Missing footprint"}
        issue = LintIssue.from_dict(d)
        assert issue.severity == "error"
        assert issue.description == "Missing footprint"

    def test_from_dict_missing_fields_use_defaults(self) -> None:
        issue = LintIssue.from_dict({})
        assert issue.severity == "unknown"
        assert issue.description == "No description"

    def test_frozen(self) -> None:
        issue = LintIssue(severity="error", description="x")
        with pytest.raises((AttributeError, TypeError)):
            issue.severity = "warning"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------


class TestValidationResult:
    def test_from_report_no_violations(self) -> None:
        result = ValidationResult.from_report({"violations": []}, returncode=0)
        assert result.passed is True
        assert result.issues == []

    def test_from_report_with_violations(self) -> None:
        report = {
            "violations": [
                {"severity": "error", "description": "Clearance violation"},
                {"severity": "warning", "description": "Silkscreen overlap"},
            ]
        }
        result = ValidationResult.from_report(report, returncode=0)
        # Has issues → not passed even if returncode==0
        assert result.passed is False
        assert len(result.issues) == 2
        assert result.error_count == 1
        assert result.warning_count == 1

    def test_from_report_non_zero_returncode(self) -> None:
        result = ValidationResult.from_report({}, returncode=1)
        assert result.passed is False

    def test_from_report_missing_violations_key(self) -> None:
        result = ValidationResult.from_report({}, returncode=0)
        assert result.passed is True
        assert result.issues == []

    def test_error_count_and_warning_count(self) -> None:
        result = ValidationResult(
            passed=False,
            issues=[
                LintIssue("error", "a"),
                LintIssue("error", "b"),
                LintIssue("warning", "c"),
                LintIssue("info", "d"),
            ],
        )
        assert result.error_count == 2
        assert result.warning_count == 1
