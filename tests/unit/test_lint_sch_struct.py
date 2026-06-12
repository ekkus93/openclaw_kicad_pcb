"""Unit tests for kicad_pcb.lint — all SCH and PCB lint rules.

Tests parse in-memory S-expression strings into AST nodes and feed them
directly to :func:`lint_schematic` / :func:`lint_pcb`.  No disk I/O is
needed.
"""

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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _pcb(body: str = "") -> ListNode:
    """Parse a minimal kicad_pcb document with optional *body* appended."""
    return parse(f"(kicad_pcb (version 20230121) (generator test)\n  {body}\n)")


# ---------------------------------------------------------------------------
# SCH001 — invalid root node
# ---------------------------------------------------------------------------


class TestSCH001:
    def test_valid_root_no_sch001(self) -> None:
        root = _sch()
        assert "SCH001" not in _codes(lint_schematic(root))

    def test_wrong_root_raises_sch001(self) -> None:
        root = parse("(kicad_pcb (version 1))")
        issues = lint_schematic(root)
        assert "SCH001" in _err_codes(issues)

    def test_sch001_stops_further_checks(self) -> None:
        """When SCH001 fires the function returns immediately."""
        root = parse("(kicad_pcb (version 1))")
        issues = lint_schematic(root)
        # Only SCH001 should be reported (no spurious downstream findings).
        assert _codes(issues) == ["SCH001"]


# ---------------------------------------------------------------------------
# SCH002 — duplicate UUIDs
# ---------------------------------------------------------------------------


class TestSCH002:
    def test_no_uuids_ok(self) -> None:
        root = _sch()
        assert "SCH002" not in _codes(lint_schematic(root))

    def test_unique_uuids_ok(self) -> None:
        root = _sch('(uuid "aaa") (uuid "bbb")')
        assert "SCH002" not in _codes(lint_schematic(root))

    def test_duplicate_uuid_triggers_sch002(self) -> None:
        root = _sch('(uuid "dup") (uuid "dup")')
        assert "SCH002" in _err_codes(lint_schematic(root))

    def test_message_contains_duplicate_id(self) -> None:
        root = _sch('(uuid "dup-id") (uuid "dup-id")')
        errs = [i for i in lint_schematic(root) if i.code == "SCH002"]
        assert any("dup-id" in i.message for i in errs)


# ---------------------------------------------------------------------------
# SCH003 — duplicate reference designators
# ---------------------------------------------------------------------------

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


class TestSCH003:
    def test_unique_refs_ok(self) -> None:
        body = _sym("R1", "u1") + "\n" + _sym("R2", "u2")
        root = parse(
            f"(kicad_sch (version 1) (generator t)\n"
            f'  (lib_symbols (symbol "Device:R"))\n'
            f"  {body}\n"
            f'  (sheet_instances (path "/"))\n'
            f")"
        )
        assert "SCH003" not in _codes(lint_schematic(root))

    def test_duplicate_ref_triggers_sch003(self) -> None:
        body = _sym("R1", "u1") + "\n" + _sym("R1", "u2")
        root = parse(
            f"(kicad_sch (version 1) (generator t)\n"
            f'  (lib_symbols (symbol "Device:R"))\n'
            f"  {body}\n"
            f'  (sheet_instances (path "/"))\n'
            f")"
        )
        assert "SCH003" in _err_codes(lint_schematic(root))

    def test_multi_unit_ref_with_distinct_units_is_allowed(self) -> None:
        body = (
            _sym("U2", "u1", lib="Device:R", unit=1)
            + "\n"
            + _sym(
                "U2",
                "u2",
                lib="Device:R",
                unit=2,
            )
        )
        root = parse(
            f"(kicad_sch (version 1) (generator t)\n"
            f'  (lib_symbols (symbol "Device:R"))\n'
            f"  {body}\n"
            f'  (sheet_instances (path "/"))\n'
            f")"
        )
        assert "SCH003" not in _err_codes(lint_schematic(root))

    def test_multi_unit_ref_with_mixed_libraries_still_triggers_sch003(self) -> None:
        body = (
            _sym("U2", "u1", lib="Device:R", unit=1)
            + "\n"
            + _sym(
                "U2",
                "u2",
                lib="Device:C",
                unit=2,
            )
        )
        root = parse(
            f"(kicad_sch (version 1) (generator t)\n"
            f'  (lib_symbols (symbol "Device:R") (symbol "Device:C"))\n'
            f"  {body}\n"
            f'  (sheet_instances (path "/"))\n'
            f")"
        )
        assert "SCH003" in _err_codes(lint_schematic(root))

    def test_message_contains_ref(self) -> None:
        body = _sym("C99", "u1") + "\n" + _sym("C99", "u2")
        root = parse(
            f"(kicad_sch (version 1) (generator t)\n"
            f'  (lib_symbols (symbol "Device:C"))\n'
            f"  {body}\n"
            f'  (sheet_instances (path "/"))\n'
            f")"
        )
        errs = [i for i in lint_schematic(root) if i.code == "SCH003"]
        assert any("C99" in i.message for i in errs)
