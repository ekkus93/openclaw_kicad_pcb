"""Phase 0: fixture validation tests using kiutils.

These tests document the four structural bugs found in generated schematics
and verify that:
  1. The working fixture loads cleanly with kiutils.
  2. Each broken fixture exhibits the known bad pattern (regression anchors).

These are NOT testing kicad_pcb.py itself yet — that comes in Phase 1+.
The fixtures serve as static regression anchors: they must not change
accidentally as refactoring proceeds.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Protocol, cast


class _KiLibSymbol(Protocol):
    entryName: str
    libraryNickname: str


class _KiSchematicSymbol(Protocol):
    entryName: str
    instances: object


class _KiSchematicDoc(Protocol):
    libSymbols: list[_KiLibSymbol]
    schematicSymbols: list[_KiSchematicSymbol]


def _load_kiutils_schematic(path: Path) -> _KiSchematicDoc:
    """Load a schematic via kiutils without importing untyped modules at type-check time."""
    schematic_mod = importlib.import_module("kiutils.schematic")
    schematic_cls = getattr(schematic_mod, "Schematic")
    doc = schematic_cls().from_file(str(path))
    return cast(_KiSchematicDoc, doc)


FIXTURES = Path(__file__).parent.parent / "fixtures"
WORKING = FIXTURES / "working"
BROKEN = FIXTURES / "broken"


# ---------------------------------------------------------------------------
# Working fixture — must load cleanly
# ---------------------------------------------------------------------------


class TestWorkingFixture:
    def test_loads_with_kiutils(self) -> None:
        sch = _load_kiutils_schematic(WORKING / "SmokeTest_R1.kicad_sch")
        assert sch is not None

    def test_has_one_lib_symbol(self) -> None:
        sch = _load_kiutils_schematic(WORKING / "SmokeTest_R1.kicad_sch")
        assert len(sch.libSymbols) == 1

    def test_lib_symbol_has_correct_root_name(self) -> None:
        sch = _load_kiutils_schematic(WORKING / "SmokeTest_R1.kicad_sch")
        lib_sym = sch.libSymbols[0]
        assert lib_sym.entryName == "R"
        assert lib_sym.libraryNickname == "Device"

    def test_sub_symbols_use_short_names(self) -> None:
        """Sub-symbols must NOT have the library prefix (e.g. 'R_0_1', not 'Device:R_0_1')."""
        raw = (WORKING / "SmokeTest_R1.kicad_sch").read_text()
        # The sub-symbols inside lib_symbols should be "R_0_1", "R_1_1" etc.
        assert '"R_0_1"' in raw
        assert '"R_1_1"' in raw
        # And MUST NOT appear with the lib prefix inside lib_symbols
        assert '"Device:R_0_1"' not in raw
        assert '"Device:R_1_1"' not in raw

    def test_no_id_properties(self) -> None:
        """Properties must not contain the old (id N) format rejected by kicad-cli 9."""
        raw = (WORKING / "SmokeTest_R1.kicad_sch").read_text()
        assert not re.search(r"\(id\s+\d+\)", raw), "Found old (id N) property format"

    def test_placed_symbol_has_instances_block(self) -> None:
        """Placed symbols must have an (instances ...) block for netlist export."""
        sch = _load_kiutils_schematic(WORKING / "SmokeTest_R1.kicad_sch")
        # Each schematic symbol should have at least one instance project path
        for sym in sch.schematicSymbols:
            assert sym.instances, f"Symbol {sym.entryName} missing (instances ...) block"


# ---------------------------------------------------------------------------
# Broken fixtures — verify each known bad pattern is still present
# (These tests PASS when the fixture still contains the bug, so we know
#  the fixture hasn't been silently "fixed".)
# ---------------------------------------------------------------------------


class TestBug1SubnameRename:
    """Bug 1: sub-symbols got library prefix — 'R_0_1' → 'Device:R_0_1'."""

    FIXTURE = BROKEN / "bug1_subname_rename.kicad_sch"

    def test_fixture_file_exists(self) -> None:
        assert self.FIXTURE.exists()

    def test_fixture_contains_bad_subname(self) -> None:
        """Regression anchor: fixture must contain the buggy sub-symbol name."""
        raw = self.FIXTURE.read_text()
        assert '"Device:R_0_1"' in raw or '"Device:R_1_1"' in raw, (
            "Bug1 fixture no longer contains the bad sub-symbol name pattern"
        )

    def test_root_symbol_name_is_correct(self) -> None:
        """Even in the broken file the root name should be 'Device:R'."""
        raw = self.FIXTURE.read_text()
        assert '(symbol "Device:R"' in raw


class TestBug2IdProperty:
    """Bug 2: properties contain old (id N) format from KiCad v20211014 libraries."""

    FIXTURE = BROKEN / "bug2_id_property.kicad_sch"

    def test_fixture_file_exists(self) -> None:
        assert self.FIXTURE.exists()

    def test_fixture_contains_id_property(self) -> None:
        """Regression anchor: fixture must contain (id N) tokens."""
        raw = self.FIXTURE.read_text()
        assert re.search(r"\(id\s+\d+\)", raw), (
            "Bug2 fixture no longer contains the old (id N) property format"
        )


class TestBug3ParenIndent:
    """Bug 3: lib_symbols closing paren at wrong indent level — structural corruption."""

    FIXTURE = BROKEN / "bug3_paren_indent.kicad_sch"

    def test_fixture_file_exists(self) -> None:
        assert self.FIXTURE.exists()

    def test_fixture_has_unindented_lib_symbols_close(self) -> None:
        """Regression anchor: lib_symbols close paren is at column 0 (wrong indent)."""
        raw = self.FIXTURE.read_text()
        lines = raw.splitlines()
        # Find the closing paren after lib_symbols content
        in_lib_symbols = False
        found_col0_close = False
        for line in lines:
            if "(lib_symbols" in line:
                in_lib_symbols = True
            if in_lib_symbols and line == ")":
                found_col0_close = True
                break
        assert found_col0_close, (
            "Bug3 fixture no longer has the unindented lib_symbols closing paren"
        )


class TestBug4NoInstances:
    """Bug 4: placed symbols missing (instances ...) block — empty netlist."""

    FIXTURE = BROKEN / "bug4_no_instances.kicad_sch"

    def test_fixture_file_exists(self) -> None:
        assert self.FIXTURE.exists()

    def test_fixture_missing_instances_block(self) -> None:
        """Regression anchor: fixture must NOT contain (instances ...) block."""
        raw = self.FIXTURE.read_text()
        # Strip comment lines (lines starting with #) before checking — the
        # fixture header itself describes the bug using the phrase "(instances"
        s_expr_only = "\n".join(
            line for line in raw.splitlines() if not line.strip().startswith("#")
        )
        assert "(instances" not in s_expr_only, (
            "Bug4 fixture unexpectedly contains an (instances ...) block"
        )
