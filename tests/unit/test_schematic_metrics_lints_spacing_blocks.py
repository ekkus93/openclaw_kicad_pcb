"""Unit tests for schematic metrics: LAY008 inter-block spacing lint."""

from __future__ import annotations

import pytest

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.lint.sch import lint_layout_crowding
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import parse as _parse_sexpr

pytestmark = pytest.mark.unit

_SCH_TMPL = "(kicad_sch {body})"


def _doc(body: str) -> SchematicDoc:
    """Build a minimal :class:`SchematicDoc` from an S-expression fragment."""
    root = _parse_sexpr(_SCH_TMPL.format(body=body))
    return SchematicDoc(root)


def _make_symbol(ref: str, x: float, y: float) -> str:
    return (
        f'(symbol (lib_id "Device:R") '
        f"(at {x} {y} 0) "
        f'(uuid "00000000-0000-0000-0000-{abs(hash(ref)):012d}") '
        f'(property "Reference" "{ref}" (at {x} {y} 0)) '
        f'(property "Value" "1k" (at {x} {y} 0)))'
    )


# ---------------------------------------------------------------------------
# TestLAY008BlockSpacingLint — Phase 2.4 inter-block spacing validation
# ---------------------------------------------------------------------------


class TestLAY008BlockSpacingLint:
    """Validate LAY008 lint rule for insufficient inter-block spacing.

    LAY008 detects when functional blocks (INPUT, OUTPUT, OPAMP_CORE, etc.)
    are closer than 20mm apart. It only runs when block_layout is provided
    (optional feature).
    """

    def test_empty_schematic_no_warnings(self) -> None:
        """Empty schematic has no blocks, so no LAY008 warnings."""
        doc = _doc("")
        issues = lint_layout_crowding(doc)
        lay008_issues = [i for i in issues if i.code == "LAY008"]
        assert lay008_issues == []

    def test_no_block_layout_skips_lay008(self) -> None:
        """LAY008 only runs when block_layout is provided; without it, skip."""
        # Create two symbols that would form blocks if assigned
        body = " ".join(
            [
                _make_symbol("R1", 10.0, 0.0),
                _make_symbol("R2", 15.0, 0.0),  # Only 5mm apart
            ]
        )
        doc = _doc(body)
        # Call without block_layout (None)
        issues = lint_layout_crowding(doc, block_layout=None)
        lay008_issues = [i for i in issues if i.code == "LAY008"]
        # LAY008 should not run without block_layout
        assert lay008_issues == []

    def test_single_block_no_warnings(self) -> None:
        """A single block type has no inter-block spacing violations."""
        # Create a symbol assigned to INPUT block
        body = _make_symbol("R1", 10.0, 0.0)
        doc = _doc(body)
        block_layout = BlockLayout()
        # Assign R1 to INPUT role
        for sym in doc.list_symbols():
            ref = sym.get("ref")
            if ref == "R1":
                block_layout.add_assignment(str(ref), BlockRole.INPUT)
        issues = lint_layout_crowding(doc, block_layout=block_layout)
        lay008_issues = [i for i in issues if i.code == "LAY008"]
        assert lay008_issues == []

    def test_blocks_exceeding_min_spacing_no_warnings(self) -> None:
        """Blocks >20mm apart should not trigger LAY008."""
        # Create two blocks 30mm apart
        body = " ".join(
            [
                _make_symbol("R1", 0.0, 0.0),  # INPUT block
                _make_symbol("R2", 30.0, 0.0),  # OUTPUT block (30mm away)
            ]
        )
        doc = _doc(body)
        block_layout = BlockLayout()
        for sym in doc.list_symbols():
            ref = sym.get("ref")
            if ref == "R1":
                block_layout.add_assignment(str(ref), BlockRole.INPUT)
            elif ref == "R2":
                block_layout.add_assignment(str(ref), BlockRole.OUTPUT)
        issues = lint_layout_crowding(doc, block_layout=block_layout)
        lay008_issues = [i for i in issues if i.code == "LAY008"]
        # 30mm > 20mm threshold, so no warning expected
        assert lay008_issues == []

    def test_blocks_below_min_spacing_triggers_warning(self) -> None:
        """Blocks <20mm apart should trigger LAY008."""
        # Create two blocks only 15mm apart
        body = " ".join(
            [
                _make_symbol("R1", 0.0, 0.0),  # INPUT block
                _make_symbol("R2", 15.0, 0.0),  # OUTPUT block (15mm away)
            ]
        )
        doc = _doc(body)
        block_layout = BlockLayout()
        for sym in doc.list_symbols():
            ref = sym.get("ref")
            if ref == "R1":
                block_layout.add_assignment(str(ref), BlockRole.INPUT)
            elif ref == "R2":
                block_layout.add_assignment(str(ref), BlockRole.OUTPUT)
        issues = lint_layout_crowding(doc, block_layout=block_layout)
        lay008_issues = [i for i in issues if i.code == "LAY008"]
        assert len(lay008_issues) > 0, "Expected LAY008 warning for blocks <20mm apart"
        # Verify the message mentions the insufficient spacing
        assert any(
            "block" in i.message.lower() and "spacing" in i.message.lower() for i in lay008_issues
        )

    def test_exactly_20mm_spacing_no_warning(self) -> None:
        """Blocks exactly 20mm apart are at the threshold; no warning."""
        # Create two blocks exactly 20mm apart
        body = " ".join(
            [
                _make_symbol("R1", 0.0, 0.0),  # INPUT block
                _make_symbol("R2", 20.0, 0.0),  # OUTPUT block (exactly 20mm)
            ]
        )
        doc = _doc(body)
        block_layout = BlockLayout()
        for sym in doc.list_symbols():
            ref = sym.get("ref")
            if ref == "R1":
                block_layout.add_assignment(str(ref), BlockRole.INPUT)
            elif ref == "R2":
                block_layout.add_assignment(str(ref), BlockRole.OUTPUT)
        issues = lint_layout_crowding(doc, block_layout=block_layout)
        lay008_issues = [i for i in issues if i.code == "LAY008"]
        # Exactly 20mm should not trigger (>= threshold)
        assert lay008_issues == []

    def test_multiple_blocks_too_close(self) -> None:
        """Multiple block pairs too close should all trigger LAY008."""
        # Create three blocks: all pairs <20mm apart
        body = " ".join(
            [
                _make_symbol("R1", 0.0, 0.0),  # INPUT at origin
                _make_symbol("R2", 10.0, 0.0),  # OUTPUT at 10mm (too close)
                _make_symbol("R3", 15.0, 0.0),  # FEEDBACK at 15mm (too close)
            ]
        )
        doc = _doc(body)
        block_layout = BlockLayout()
        for sym in doc.list_symbols():
            ref = sym.get("ref")
            if ref == "R1":
                block_layout.add_assignment(str(ref), BlockRole.INPUT)
            elif ref == "R2":
                block_layout.add_assignment(str(ref), BlockRole.OUTPUT)
            elif ref == "R3":
                block_layout.add_assignment(str(ref), BlockRole.FEEDBACK)
        issues = lint_layout_crowding(doc, block_layout=block_layout)
        lay008_issues = [i for i in issues if i.code == "LAY008"]
        # Expect warnings for too-close pairs
        assert len(lay008_issues) > 0, "Expected LAY008 warnings for multiple close block pairs"


# ---------------------------------------------------------------------------
# TestLAY012PageBalanceLint / TestLAY013CompositionLint — Phase 8.3
# ---------------------------------------------------------------------------
