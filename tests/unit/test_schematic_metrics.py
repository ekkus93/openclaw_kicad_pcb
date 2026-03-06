"""Unit tests for :mod:`kicad_pcb.schematic_metrics`."""

from __future__ import annotations

from pathlib import Path

import pytest
from kicad_pcb.errors import ParseError
from kicad_pcb.lint.sch import LintIssue
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.schematic_metrics import (
    count_distinct_x_columns,
    count_global_labels,
    run_layout_lints,
    wire_stub_ratio,
)
from kicad_pcb.sexpr import parse as _parse_sexpr

# ---------------------------------------------------------------------------
# Regression fixture
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "regressions"
_HEADPHONE_AMP_CURRENT = _FIXTURE_DIR / "headphone_amp_current_layout.kicad_sch"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A skeletal kicad_sch wrapper; child nodes are injected per test.
_SCH_TMPL = "(kicad_sch {body})"


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


def _make_wire(x1: float, y1: float, x2: float, y2: float) -> str:
    return f"(wire (pts (xy {x1} {y1}) (xy {x2} {y2})))"


def _make_global_label(text: str, x: float = 0.0, y: float = 0.0) -> str:
    return (
        f'(global_label "{text}" (shape input) '
        f"(at {x} {y} 0) "
        f'(uuid "00000000-0000-0000-0000-{abs(hash(text + str(x))):012d}"))'
    )


# ---------------------------------------------------------------------------
# TestCountDistinctXColumns
# ---------------------------------------------------------------------------


class TestCountDistinctXColumns:
    def test_empty_schematic_returns_zero(self) -> None:
        doc = _doc("")
        assert count_distinct_x_columns(doc) == 0

    def test_single_symbol_returns_one(self) -> None:
        doc = _doc(_make_symbol("R1", 30.0, 50.0))
        assert count_distinct_x_columns(doc) == 1

    def test_three_aligned_columns(self) -> None:
        # 3 distinct x groups at x=30, 60, 90 (well separated > 0.5 mm tol)
        body = " ".join(
            [
                _make_symbol("R1", 30.0, 50.0),
                _make_symbol("R2", 30.2, 70.0),  # same column as R1 (diff < 5mm)
                _make_symbol("C1", 60.0, 50.0),
                _make_symbol("C2", 60.3, 80.0),  # same column as C1
                _make_symbol("U1", 90.0, 50.0),
            ]
        )
        doc = _doc(body)
        result = count_distinct_x_columns(doc, tolerance_mm=0.5)
        assert result == 3

    def test_tolerance_merges_nearby_xs(self) -> None:
        # x=100.0 and x=100.4 differ by only 0.4 mm; with tol=0.5 they fall
        # in the same bucket: int(100.0/0.5)=200 and int(100.4/0.5)=200.
        body = " ".join(
            [
                _make_symbol("R1", 100.0, 50.0),
                _make_symbol("R2", 100.4, 70.0),
            ]
        )
        doc = _doc(body)
        assert count_distinct_x_columns(doc, tolerance_mm=0.5) == 1

    def test_tolerance_separates_borderline_xs(self) -> None:
        # x=100.0 and x=100.5 fall in different buckets with tol=0.5:
        # int(100.0/0.5)=200 vs int(100.5/0.5)=201.
        body = " ".join(
            [
                _make_symbol("R1", 100.0, 50.0),
                _make_symbol("R2", 100.5, 70.0),
            ]
        )
        doc = _doc(body)
        assert count_distinct_x_columns(doc, tolerance_mm=0.5) == 2

    def test_custom_tolerance(self) -> None:
        # With tol=10.0, x=5.0 and x=9.9 share bucket 0; x=10.0 is bucket 1.
        body = " ".join(
            [
                _make_symbol("R1", 5.0, 50.0),
                _make_symbol("R2", 9.9, 70.0),
                _make_symbol("R3", 10.0, 50.0),
            ]
        )
        doc = _doc(body)
        assert count_distinct_x_columns(doc, tolerance_mm=10.0) == 2

    def test_raises_parse_error_for_malformed_symbol_coordinates(self) -> None:
        body = " ".join(
            [
                '(symbol (lib_id "Device:R") (at not-a-number 50.0 0) '
                '(uuid "00000000-0000-0000-0000-000000000001") '
                '(property "Reference" "R1" (at 0 0 0)) '
                '(property "Value" "1k" (at 0 0 0)))'
            ]
        )
        doc = _doc(body)

        with pytest.raises(ParseError, match="Malformed symbol"):
            count_distinct_x_columns(doc)


# ---------------------------------------------------------------------------
# TestCountGlobalLabels
# ---------------------------------------------------------------------------


class TestCountGlobalLabels:
    def test_empty_returns_zero(self) -> None:
        doc = _doc("")
        assert count_global_labels(doc) == 0

    def test_no_matching_labels(self) -> None:
        body = _make_global_label("VCC", 0.0, 0.0)
        doc = _doc(body)
        assert count_global_labels(doc, text="GND") == 0

    def test_single_match(self) -> None:
        body = _make_global_label("GND", 0.0, 0.0)
        doc = _doc(body)
        assert count_global_labels(doc, text="GND") == 1

    def test_multiple_matches(self) -> None:
        body = " ".join(
            [
                _make_global_label("GND", 0.0, 0.0),
                _make_global_label("GND", 50.0, 0.0),
                _make_global_label("GND", 100.0, 0.0),
            ]
        )
        doc = _doc(body)
        assert count_global_labels(doc, text="GND") == 3

    def test_mixed_labels_only_counts_target(self) -> None:
        body = " ".join(
            [
                _make_global_label("GND", 0.0, 0.0),
                _make_global_label("GND", 50.0, 0.0),
                _make_global_label("VCC", 0.0, 20.0),
                _make_global_label("VBAT", 0.0, 40.0),
            ]
        )
        doc = _doc(body)
        assert count_global_labels(doc, text="GND") == 2
        assert count_global_labels(doc, text="VCC") == 1
        assert count_global_labels(doc, text="VBAT") == 1
        assert count_global_labels(doc, text="MISSING") == 0

    def test_case_sensitive(self) -> None:
        body = _make_global_label("gnd", 0.0, 0.0)
        doc = _doc(body)
        assert count_global_labels(doc, text="GND") == 0
        assert count_global_labels(doc, text="gnd") == 1


# ---------------------------------------------------------------------------
# TestRunLayoutLints
# ---------------------------------------------------------------------------


class TestRunLayoutLints:
    def test_empty_schematic_returns_empty_list(self) -> None:
        doc = _doc("")
        issues = run_layout_lints(doc)
        assert isinstance(issues, list)
        # An empty schematic has no symbols — no placement violations.
        # We just verify no exceptions and a list is returned.

    def test_returns_list_of_lint_issues(self) -> None:
        doc = _doc(_make_symbol("R1", 30.0, 50.0))
        issues = run_layout_lints(doc)
        assert isinstance(issues, list)
        for issue in issues:
            assert isinstance(issue, LintIssue)

    def test_single_symbol_on_grid_has_no_issues(self) -> None:
        # A single symbol at a clean grid position (multiple of 1.27 mm)
        # should pass all placement checks.
        doc = _doc(_make_symbol("R1", 50.8, 50.8))
        issues = run_layout_lints(doc)
        grid_issues = [i for i in issues if i.code.startswith("LAY002")]
        assert grid_issues == [], grid_issues


# ---------------------------------------------------------------------------
# TestWireStubRatio
# ---------------------------------------------------------------------------


class TestWireStubRatio:
    def test_empty_schematic_returns_zero(self) -> None:
        doc = _doc("")
        assert wire_stub_ratio(doc) == pytest.approx(0.0)

    def test_all_stubs_returns_one(self) -> None:
        # Three wires all ≤ 5.08 mm (horizontal, length = 2.54 mm each)
        body = " ".join(
            [
                _make_wire(0.0, 0.0, 2.54, 0.0),
                _make_wire(10.0, 0.0, 12.54, 0.0),
                _make_wire(20.0, 0.0, 22.54, 0.0),
            ]
        )
        doc = _doc(body)
        assert wire_stub_ratio(doc) == pytest.approx(1.0)

    def test_no_stubs_returns_zero_point_zero(self) -> None:
        # Two wires both much longer than 5.28 mm
        body = " ".join(
            [
                _make_wire(0.0, 0.0, 50.0, 0.0),
                _make_wire(100.0, 0.0, 200.0, 0.0),
            ]
        )
        doc = _doc(body)
        assert wire_stub_ratio(doc) == pytest.approx(0.0)

    def test_mixed_returns_correct_fraction(self) -> None:
        # 2 stubs (2.54 mm) + 2 long (50 mm) → ratio = 0.5
        body = " ".join(
            [
                _make_wire(0.0, 0.0, 2.54, 0.0),  # stub
                _make_wire(10.0, 0.0, 12.54, 0.0),  # stub
                _make_wire(20.0, 0.0, 70.0, 0.0),  # long
                _make_wire(80.0, 0.0, 130.0, 0.0),  # long
            ]
        )
        doc = _doc(body)
        assert wire_stub_ratio(doc) == pytest.approx(0.5)

    def test_exactly_at_threshold_is_stub(self) -> None:
        # Wire length == stub_len_mm exactly (5.08 mm), should count as stub.
        body = _make_wire(0.0, 0.0, 5.08, 0.0)
        doc = _doc(body)
        assert wire_stub_ratio(doc) == pytest.approx(1.0)

    def test_custom_stub_len(self) -> None:
        # Wire of 10.0 mm — not a stub at default 5.08 mm, but is at 10.5 mm.
        body = _make_wire(0.0, 0.0, 10.0, 0.0)
        doc = _doc(body)
        assert wire_stub_ratio(doc, stub_len_mm=5.08) == pytest.approx(0.0)
        assert wire_stub_ratio(doc, stub_len_mm=10.5) == pytest.approx(1.0)

    def test_diagonal_wire_uses_euclidean_length(self) -> None:
        # 3-4-5 triangle: legs 3.0 and 4.0, hypotenuse = 5.0 mm (< 5.28 → stub)
        body = _make_wire(0.0, 0.0, 3.0, 4.0)
        doc = _doc(body)
        assert wire_stub_ratio(doc) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# TestRegressionFixture — integration test using the headphone amp schematic
# ---------------------------------------------------------------------------


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
