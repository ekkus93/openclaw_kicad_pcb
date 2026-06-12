"""Unit tests for kicad_pcb.lint — SCH004 through SCH010 lint rules."""

from __future__ import annotations

import pytest

from kicad_pcb.lint import (
    LintIssue,
    LintSeverity,
    lint_schematic,
)
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import ListNode

pytestmark = pytest.mark.unit

_ERR = LintSeverity.ERROR
_WARN = LintSeverity.WARNING


def _codes(issues: list[LintIssue]) -> list[str]:
    return [i.code for i in issues]


def _err_codes(issues: list[LintIssue]) -> list[str]:
    return [i.code for i in issues if i.severity == _ERR]


def _warn_codes(issues: list[LintIssue]) -> list[str]:
    return [i.code for i in issues if i.severity == _WARN]


def _sch(body: str = "") -> ListNode:
    """Parse a minimal kicad_sch document with optional *body* appended."""
    return parse(
        f"(kicad_sch (version 20230121) (generator test)\n"
        f"  (lib_symbols)\n"
        f"  {body}\n"
        f'  (sheet_instances (path "/" (page "1")))\n'
        f")"
    )


_SYM_TMPL = """\
(symbol (lib_id "{lib}") (at 50 76 0) (unit {unit}) (uuid "{uuid}")
  (property "Reference" "{ref}" (at 0 0 0))
  (property "Value" "{val}" (at 0 0 0))
)"""


def _sym(
    ref: str,
    uuid: str,
    lib: str = "Device:R",
    val: str = "10k",
    *,
    unit: int = 1,
) -> str:
    return _SYM_TMPL.format(lib=lib, uuid=uuid, ref=ref, val=val, unit=unit)


# ---------------------------------------------------------------------------
# SCH004 — symbol missing Reference property
# ---------------------------------------------------------------------------


class TestSCH004:
    _NO_REF = """\
(symbol (lib_id "Device:R") (at 50 76 0) (unit 1) (uuid "u1")
  (property "Value" "10k" (at 0 0 0))
)"""

    def test_missing_reference_triggers_sch004(self) -> None:
        root = parse(
            f"(kicad_sch (version 1) (generator t)\n"
            f'  (lib_symbols (symbol "Device:R"))\n'
            f"  {self._NO_REF}\n"
            f'  (sheet_instances (path "/"))\n'
            f")"
        )
        assert "SCH004" in _err_codes(lint_schematic(root))

    def test_with_reference_no_sch004(self) -> None:
        body = _sym("R1", "u1")
        root = parse(
            f"(kicad_sch (version 1) (generator t)\n"
            f'  (lib_symbols (symbol "Device:R"))\n'
            f"  {body}\n"
            f'  (sheet_instances (path "/"))\n'
            f")"
        )
        assert "SCH004" not in _codes(lint_schematic(root))


# ---------------------------------------------------------------------------
# SCH005 — symbol missing Value property
# ---------------------------------------------------------------------------


class TestSCH005:
    _NO_VAL = """\
(symbol (lib_id "Device:R") (at 50 76 0) (unit 1) (uuid "u1")
  (property "Reference" "R1" (at 0 0 0))
)"""

    def test_missing_value_triggers_sch005(self) -> None:
        root = parse(
            f"(kicad_sch (version 1) (generator t)\n"
            f'  (lib_symbols (symbol "Device:R"))\n'
            f"  {self._NO_VAL}\n"
            f'  (sheet_instances (path "/"))\n'
            f")"
        )
        assert "SCH005" in _err_codes(lint_schematic(root))


# ---------------------------------------------------------------------------
# SCH006 — malformed (at …) node
# ---------------------------------------------------------------------------


class TestSCH006:
    def test_valid_at_no_sch006(self) -> None:
        root = _sch("(at 10.0 20.0 0)")
        assert "SCH006" not in _codes(lint_schematic(root))

    def test_insufficient_args_triggers_sch006(self) -> None:
        root = _sch("(at 10.0)")
        assert "SCH006" in _err_codes(lint_schematic(root))

    def test_non_numeric_x_triggers_sch006(self) -> None:
        root = _sch('(at "bad" 20.0 0)')
        assert "SCH006" in _err_codes(lint_schematic(root))

    def test_non_numeric_y_triggers_sch006(self) -> None:
        root = _sch('(at 10.0 "bad" 0)')
        assert "SCH006" in _err_codes(lint_schematic(root))

    def test_valid_at_with_angle_no_sch006(self) -> None:
        root = _sch("(at 10.0 20.0 90)")
        assert "SCH006" not in _codes(lint_schematic(root))


# ---------------------------------------------------------------------------
# SCH007 — malformed wire (pts …)
# ---------------------------------------------------------------------------


class TestSCH007:
    def test_valid_wire_no_sch007(self) -> None:
        root = _sch('(wire (pts (xy 0 0) (xy 10 0)) (stroke) (uuid "w1"))')
        assert "SCH007" not in _codes(lint_schematic(root))

    def test_wire_missing_pts_triggers_sch007(self) -> None:
        root = _sch('(wire (uuid "w1"))')
        assert "SCH007" in _err_codes(lint_schematic(root))

    def test_wire_one_xy_triggers_sch007(self) -> None:
        root = _sch('(wire (pts (xy 0 0)) (uuid "w1"))')
        assert "SCH007" in _err_codes(lint_schematic(root))

    def test_wire_non_numeric_xy_triggers_sch007(self) -> None:
        root = _sch('(wire (pts (xy "x" "y") (xy 10 0)) (uuid "w1"))')
        assert "SCH007" in _err_codes(lint_schematic(root))


# ---------------------------------------------------------------------------
# SCH008 — lib_symbols missing/empty when placed symbols exist
# ---------------------------------------------------------------------------


class TestSCH008:
    def test_no_symbols_no_sch008(self) -> None:
        root = _sch()
        assert "SCH008" not in _codes(lint_schematic(root))

    def test_symbols_with_lib_symbols_section_no_sch008(self) -> None:
        body = _sym("R1", "u1")
        root = parse(
            f"(kicad_sch (version 1) (generator t)\n"
            f'  (lib_symbols (symbol "Device:R"))\n'
            f"  {body}\n"
            f'  (sheet_instances (path "/"))\n'
            f")"
        )
        assert "SCH008" not in _codes(lint_schematic(root))

    def test_symbols_missing_lib_symbols_triggers_sch008_error(self) -> None:
        body = _sym("R1", "u1")
        root = parse(
            f'(kicad_sch (version 1) (generator t)\n  {body}\n  (sheet_instances (path "/"))\n)'
        )
        assert "SCH008" in _err_codes(lint_schematic(root))

    def test_symbols_empty_lib_symbols_triggers_sch008_warning(self) -> None:
        body = _sym("R1", "u1")
        root = parse(
            f"(kicad_sch (version 1) (generator t)\n"
            f"  (lib_symbols)\n"
            f"  {body}\n"
            f'  (sheet_instances (path "/"))\n'
            f")"
        )
        assert "SCH008" in _warn_codes(lint_schematic(root))


# ---------------------------------------------------------------------------
# SCH009 — lib_id not found in embedded lib_symbols
# ---------------------------------------------------------------------------


class TestSCH009:
    def test_lib_id_present_no_sch009(self) -> None:
        body = _sym("R1", "u1", lib="Device:R")
        root = parse(
            f"(kicad_sch (version 1) (generator t)\n"
            f'  (lib_symbols (symbol "Device:R"))\n'
            f"  {body}\n"
            f'  (sheet_instances (path "/"))\n'
            f")"
        )
        assert "SCH009" not in _codes(lint_schematic(root))

    def test_lib_id_missing_triggers_sch009(self) -> None:
        body = _sym("R1", "u1", lib="Device:R")
        root = parse(
            f"(kicad_sch (version 1) (generator t)\n"
            f'  (lib_symbols (symbol "Device:C"))\n'
            f"  {body}\n"
            f'  (sheet_instances (path "/"))\n'
            f")"
        )
        assert "SCH009" in _err_codes(lint_schematic(root))

    def test_message_contains_lib_id(self) -> None:
        body = _sym("R1", "u1", lib="Device:LED")
        root = parse(
            f"(kicad_sch (version 1) (generator t)\n"
            f'  (lib_symbols (symbol "Device:R"))\n'
            f"  {body}\n"
            f'  (sheet_instances (path "/"))\n'
            f")"
        )
        errs = [i for i in lint_schematic(root) if i.code == "SCH009"]
        assert any("Device:LED" in i.message for i in errs)


# ---------------------------------------------------------------------------
# SCH010 — net/power label missing (at …) placement
# ---------------------------------------------------------------------------


class TestSCH010:
    def test_label_missing_at_is_error(self) -> None:
        root = _sch('(label "NET")')
        assert "SCH010" in _err_codes(lint_schematic(root))

    def test_global_label_missing_at_is_error(self) -> None:
        root = _sch('(global_label "GLOBAL_NET")')
        assert "SCH010" in _err_codes(lint_schematic(root))

    def test_label_with_at_is_clean(self) -> None:
        root = _sch('(label "NET" (at 10 20 0))')
        assert "SCH010" not in _codes(lint_schematic(root))
