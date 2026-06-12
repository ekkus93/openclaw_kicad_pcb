from __future__ import annotations

import pytest

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.lint import LINT_SUGGESTIONS
from kicad_pcb.lint.sch import lint_layout_composition
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import parse as _parse_sexpr

_SCH_TMPL = "(kicad_sch {body})"


pytestmark = pytest.mark.unit


def _doc(body: str) -> SchematicDoc:
    """Build a minimal :class:`SchematicDoc` from an S-expression fragment."""
    root = _parse_sexpr(_SCH_TMPL.format(body=body))
    return SchematicDoc(root)


# Adds symbols at arbitrary x,y positions only (no geometry).  KiCad symbol
# nodes have many more children in production; the metrics code only uses the
# at-node x,y coordinates extracted by SchematicDoc.list_symbols().


def _make_symbol(ref: str, x: float, y: float) -> str:
    return (
        f'(symbol (lib_id "Device:R") '
        f"(at {x} {y} 0) "
        f'(uuid "00000000-0000-0000-0000-{abs(hash(ref)):012d}") '
        f'(property "Reference" "{ref}" (at {x} {y} 0)) '
        f'(property "Value" "1k" (at {x} {y} 0)))'
    )


class TestLAY012PageBalanceLint:
    def test_lay012_suggestion_registered(self) -> None:
        assert "LAY012" in LINT_SUGGESTIONS
        assert len(LINT_SUGGESTIONS["LAY012"]) > 10

    def test_balanced_quadrants_do_not_trigger_lay012(self) -> None:
        body = " ".join(
            [
                _make_symbol("R1", 50.0, 70.0),
                _make_symbol("R2", 220.0, 70.0),
                _make_symbol("R3", 50.0, 170.0),
                _make_symbol("R4", 220.0, 170.0),
            ]
        )
        issues = lint_layout_composition(_doc(body))
        assert [i for i in issues if i.code == "LAY012"] == []

    def test_dense_single_quadrant_triggers_lay012(self) -> None:
        body = " ".join(
            [
                _make_symbol("R1", 40.0, 60.0),
                _make_symbol("R2", 45.0, 65.0),
                _make_symbol("R3", 50.0, 70.0),
                _make_symbol("R4", 55.0, 75.0),
                _make_symbol("R5", 60.0, 80.0),
                _make_symbol("R6", 65.0, 85.0),
                _make_symbol("R7", 240.0, 185.0),
                _make_symbol("R8", 245.0, 190.0),
            ]
        )
        issues = lint_layout_composition(_doc(body))
        lay012 = [i for i in issues if i.code == "LAY012"]
        assert len(lay012) == 1
        assert "unbalanced" in lay012[0].message.lower()


class TestLAY013CompositionLint:
    def test_lay013_suggestion_registered(self) -> None:
        assert "LAY013" in LINT_SUGGESTIONS
        assert len(LINT_SUGGESTIONS["LAY013"]) > 10

    def test_title_block_encroachment_triggers_lay013(self) -> None:
        body = " ".join(
            [
                _make_symbol("U1", 120.0, 180.0),
                _make_symbol("R1", 95.0, 150.0),
            ]
        )
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R1", BlockRole.FEEDBACK)
        issues = lint_layout_composition(_doc(body), block_layout=block_layout)
        lay013 = [i for i in issues if i.code == "LAY013"]
        assert any("title-block" in i.message.lower() for i in lay013)

    def test_opamp_too_low_triggers_lay013(self) -> None:
        body = " ".join(
            [
                _make_symbol("U1", 150.0, 190.0),
                _make_symbol("R1", 130.0, 175.0),
                _make_symbol("J1", 80.0, 165.0),
            ]
        )
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R1", BlockRole.FEEDBACK)
        block_layout.add_assignment("J1", BlockRole.INPUT)
        issues = lint_layout_composition(_doc(body), block_layout=block_layout)
        lay013 = [i for i in issues if i.code == "LAY013"]
        assert any("too low" in i.message.lower() for i in lay013)

    def test_opamp_centered_does_not_trigger_opamp_message(self) -> None:
        body = " ".join(
            [
                _make_symbol("U1", 150.0, 125.0),
                _make_symbol("R1", 130.0, 105.0),
                _make_symbol("J1", 80.0, 145.0),
                _make_symbol("J2", 230.0, 125.0),
            ]
        )
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R1", BlockRole.FEEDBACK)
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)
        issues = lint_layout_composition(_doc(body), block_layout=block_layout)
        lay013 = [i for i in issues if i.code == "LAY013"]
        assert all("op-amp stage is too" not in i.message.lower() for i in lay013)

    def test_small_vertical_span_triggers_lay013(self) -> None:
        body = " ".join(
            [
                _make_symbol("U1", 150.0, 120.0),
                _make_symbol("R1", 130.0, 122.0),
                _make_symbol("J1", 80.0, 124.0),
            ]
        )
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R1", BlockRole.FEEDBACK)
        block_layout.add_assignment("J1", BlockRole.INPUT)
        issues = lint_layout_composition(_doc(body), block_layout=block_layout)
        lay013 = [i for i in issues if i.code == "LAY013"]
        assert any("vertical span is too small" in i.message.lower() for i in lay013)

    def test_without_block_layout_still_checks_title_block(self) -> None:
        body = " ".join(
            [
                _make_symbol("R1", 110.0, 182.0),
                _make_symbol("R2", 130.0, 140.0),
            ]
        )
        issues = lint_layout_composition(_doc(body), block_layout=None)
        lay013 = [i for i in issues if i.code == "LAY013"]
        assert any("title-block" in i.message.lower() for i in lay013)
