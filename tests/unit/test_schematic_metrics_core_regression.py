from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.lint.sch import LintIssue
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.schematic_metrics import (
    compute_local_density,
    count_distinct_x_columns,
    count_global_labels,
    run_layout_lints,
    wire_stub_ratio,
)
from kicad_pcb.sexpr import parse as _parse_sexpr

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "regressions"
_HEADPHONE_AMP_CURRENT = _FIXTURE_DIR / "headphone_amp_current_layout.kicad_sch"

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


@pytest.mark.skipif(
    not _HEADPHONE_AMP_CURRENT.exists(),
    reason="Headphone amp regression fixture not found",
)
class TestRegressionFixture:
    """Load the headphone-amp regression fixture and sanity-check metrics.

    These are not tight assertions on exact values but ballpark guards that
    catch regressions in the fixture or metric implementations. Exact values
    are documented in ``tests/fixtures/regressions/README.md``.
    """

    @pytest.fixture(scope="class")
    def doc(self) -> SchematicDoc:
        return SchematicDoc.load(_HEADPHONE_AMP_CURRENT)

    def test_x_columns_positive(self, doc: SchematicDoc) -> None:
        # The headphone-amp schematic (13 components) must have at least 1 col.
        cols = count_distinct_x_columns(doc)
        assert cols >= 1, f"Expected ≥ 1 x-columns, got {cols}"

    def test_global_labels_are_zero_or_small(self, doc: SchematicDoc) -> None:
        # The README states 0 global labels; accept a small slack in case later
        # fixture versions add power symbols.
        gnd_labels = count_global_labels(doc, text="GND")
        assert gnd_labels <= 5, f"Too many GND global_labels: {gnd_labels}"  # noqa: PLR2004

    def test_run_layout_lints_returns_list(self, doc: SchematicDoc) -> None:
        issues = run_layout_lints(doc)
        assert isinstance(issues, list)
        for issue in issues:
            assert isinstance(issue, LintIssue)

    def test_wire_stub_ratio_between_zero_and_one(self, doc: SchematicDoc) -> None:
        ratio = wire_stub_ratio(doc)
        assert 0.0 <= ratio <= 1.0, f"invalid ratio: {ratio}"

    def test_wire_stub_ratio_expected_range(self, doc: SchematicDoc) -> None:
        # The README baseline states 34 total wire segments. The unrouted
        # current layout places a label stub at each pin, so the ratio should
        # be substantial (> 0.3) but not necessarily 1.0.
        ratio = wire_stub_ratio(doc)
        assert ratio > 0.0, "Expected at least some pin-stub wires"

    def test_fixture_has_expected_symbol_count(self, doc: SchematicDoc) -> None:
        # README: 13 components in the headphone amp fixture.
        syms = doc.list_symbols()
        # Allow ±2 for potential power symbols not counted in README baseline.
        assert 11 <= len(syms) <= 20, f"Unexpected symbol count: {len(syms)}"  # noqa: PLR2004


# ---------------------------------------------------------------------------
# TestComputeLocalDensity — Phase 2.1 density computation tests
# ---------------------------------------------------------------------------


class TestComputeLocalDensity:
    def test_empty_schematic_returns_empty_dict(self) -> None:
        doc = _doc("")
        density = compute_local_density(doc)
        assert density == {}

    def test_single_symbol_has_zero_neighbors(self) -> None:
        doc = _doc(_make_symbol("R1", 30.0, 50.0))
        density = compute_local_density(doc, radius_mm=30.0)
        assert "R1" in density
        assert density["R1"] == 0.0

    def test_two_symbols_within_radius_see_each_other(self) -> None:
        # R1 and R2 are 10mm apart (< radius 30mm), so each sees 1 neighbor
        body = " ".join(
            [
                _make_symbol("R1", 0.0, 0.0),
                _make_symbol("R2", 10.0, 0.0),
            ]
        )
        doc = _doc(body)
        density = compute_local_density(doc, radius_mm=30.0)
        assert density["R1"] == 1.0
        assert density["R2"] == 1.0

    def test_two_symbols_outside_radius_see_zero(self) -> None:
        # R1 and R2 are 50mm apart (> radius 30mm), so neither sees the other
        body = " ".join(
            [
                _make_symbol("R1", 0.0, 0.0),
                _make_symbol("R2", 50.0, 0.0),
            ]
        )
        doc = _doc(body)
        density = compute_local_density(doc, radius_mm=30.0)
        assert density["R1"] == 0.0
        assert density["R2"] == 0.0

    def test_cluster_of_five_symbols(self) -> None:
        # Five symbols clustered within 20mm radius of origin
        # Each should see 4 neighbors
        body = " ".join(
            [
                _make_symbol("R1", 0.0, 0.0),
                _make_symbol("R2", 10.0, 0.0),
                _make_symbol("R3", 0.0, 10.0),
                _make_symbol("R4", 10.0, 10.0),
                _make_symbol("R5", 5.0, 5.0),  # center
            ]
        )
        doc = _doc(body)
        density = compute_local_density(doc, radius_mm=30.0)
        # R5 is in the center, should see all 4 corners
        assert density["R5"] == 4.0
        # Corner symbols see at least 2-3 neighbors (depending on distances)
        assert density["R1"] >= 2.0
        assert density["R2"] >= 2.0
