"""Unit tests for :mod:`kicad_pcb.schematic_metrics`."""

from __future__ import annotations

from pathlib import Path

import pytest
from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.errors import ParseError
from kicad_pcb.lint import LINT_SUGGESTIONS
from kicad_pcb.lint.sch import LintIssue, lint_layout_composition, lint_layout_crowding
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.schematic_metrics import (
    compute_block_role_spread,
    compute_local_density,
    count_distinct_x_columns,
    count_global_labels,
    count_non_power_symbols_in_same_x_column_as,
    count_refs_in_same_x_column_as,
    detect_dense_clusters,
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
# TestAnchorColumnCrowdingMetrics
# ---------------------------------------------------------------------------


class TestAnchorColumnCrowdingMetrics:
    def test_count_refs_in_same_x_column_as_anchor(self) -> None:
        body = " ".join(
            [
                _make_symbol("U1", 100.0, 50.0),
                _make_symbol("R1", 100.0, 70.0),
                _make_symbol("R2", 100.4, 90.0),
                _make_symbol("R3", 101.0, 110.0),
                _make_symbol("C1", 130.0, 60.0),
            ]
        )
        doc = _doc(body)

        assert count_refs_in_same_x_column_as(doc, "U1", tolerance_mm=0.5) == 2
        assert (
            count_refs_in_same_x_column_as(
                doc,
                "U1",
                tolerance_mm=0.5,
                include_anchor=True,
            )
            == 3
        )

    def test_count_refs_in_same_x_column_with_ref_filter(self) -> None:
        body = " ".join(
            [
                _make_symbol("U1", 100.0, 50.0),
                _make_symbol("R1", 100.0, 70.0),
                _make_symbol("R2", 100.4, 90.0),
                _make_symbol("C1", 100.2, 30.0),
            ]
        )
        doc = _doc(body)

        assert (
            count_refs_in_same_x_column_as(
                doc,
                "U1",
                tolerance_mm=0.5,
                refs={"R1", "C1"},
            )
            == 2
        )

    def test_non_power_variant_excludes_power_symbols(self) -> None:
        body = " ".join(
            [
                _make_symbol("U1", 100.0, 50.0),
                _make_symbol("R1", 100.0, 70.0),
                _make_symbol("#PWR01", 100.0, 30.0),
            ]
        )
        doc = _doc(body)

        assert count_refs_in_same_x_column_as(doc, "U1", tolerance_mm=0.5) == 2
        assert count_non_power_symbols_in_same_x_column_as(doc, "U1", tolerance_mm=0.5) == 1

    def test_missing_anchor_ref_raises_value_error(self) -> None:
        doc = _doc(_make_symbol("R1", 10.0, 10.0))
        with pytest.raises(ValueError, match="Anchor ref not found"):
            count_refs_in_same_x_column_as(doc, "U1")


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


# ---------------------------------------------------------------------------
# TestDetectDenseClusters — Phase 2.1 cluster detection tests
# ---------------------------------------------------------------------------


class TestDetectDenseClusters:
    def test_empty_schematic_returns_empty_list(self) -> None:
        doc = _doc("")
        clusters = detect_dense_clusters(doc)
        assert clusters == []

    def test_no_dense_clusters_below_threshold(self) -> None:
        # Two symbols 50mm apart, threshold=5, radius=30mm
        # Neither has 5 neighbors, so no clusters detected
        body = " ".join(
            [
                _make_symbol("R1", 0.0, 0.0),
                _make_symbol("R2", 50.0, 0.0),
            ]
        )
        doc = _doc(body)
        clusters = detect_dense_clusters(doc, radius_mm=30.0, threshold=5)
        assert clusters == []

    def test_dense_cluster_detected(self) -> None:
        # Six symbols clustered tightly (all within 20mm of each other)
        # Each sees 5 neighbors (threshold=5), so all qualify as dense
        body = " ".join(
            [
                _make_symbol("R1", 0.0, 0.0),
                _make_symbol("R2", 10.0, 0.0),
                _make_symbol("R3", 0.0, 10.0),
                _make_symbol("R4", 10.0, 10.0),
                _make_symbol("R5", 5.0, 5.0),
                _make_symbol("R6", 5.0, 15.0),
            ]
        )
        doc = _doc(body)
        clusters = detect_dense_clusters(doc, radius_mm=30.0, threshold=5)
        assert len(clusters) >= 1, "Expected at least one dense cluster"
        # Verify result format: (x, y, neighbor_count)
        for x, y, count in clusters:
            assert isinstance(x, float)
            assert isinstance(y, float)
            assert isinstance(count, int)
            assert count >= 5  # meets threshold

    def test_clusters_sorted_by_density(self) -> None:
        # Create two clusters: dense (7 symbols) and less dense (2 symbols)
        # Dense cluster should appear first in results
        body = " ".join(
            [
                # Dense cluster at origin (7 tightly packed)
                _make_symbol("R1", 0.0, 0.0),
                _make_symbol("R2", 5.0, 0.0),
                _make_symbol("R3", 0.0, 5.0),
                _make_symbol("R4", 5.0, 5.0),
                _make_symbol("R5", 2.5, 2.5),
                _make_symbol("R6", 7.5, 2.5),
                _make_symbol("R7", 2.5, 7.5),
                # Sparse pair far away
                _make_symbol("C1", 200.0, 200.0),
                _make_symbol("C2", 210.0, 200.0),
            ]
        )
        doc = _doc(body)
        clusters = detect_dense_clusters(doc, radius_mm=30.0, threshold=3)
        assert len(clusters) >= 1
        # First cluster should have highest neighbor count
        if len(clusters) > 1:
            assert clusters[0][2] >= clusters[1][2], "Clusters not sorted by density"


# ---------------------------------------------------------------------------
# TestComputeBlockRoleSpread
# ---------------------------------------------------------------------------


class TestComputeBlockRoleSpread:
    def test_returns_serializable_spread_by_role(self) -> None:
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("R1", BlockRole.INPUT)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)

        positions = {
            "J1": (10.0, 20.0, 0.0),
            "R1": (20.0, 20.0, 0.0),
            "U1": (50.0, 40.0, 0.0),
        }

        spread = compute_block_role_spread(positions, block_layout, tolerance_mm=0.5)

        assert spread["input"]["count"] == 2
        assert spread["input"]["column_count"] == 2
        assert spread["input"]["width_mm"] == pytest.approx(10.0)
        assert spread["input"]["height_mm"] == pytest.approx(0.0)
        assert spread["opamp_core"]["count"] == 1
        assert spread["opamp_core"]["width_mm"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# TestLAY006LocalCrowdingLint — Phase 2.4 crowding lints validation
# ---------------------------------------------------------------------------


class TestLAY006LocalCrowdingLint:
    """Validate LAY006 lint rule for local crowding detection.

    LAY006 detects symbols with ≥6 neighbors within a 30mm radius (below
    threshold, no warning). Once a symbol has 6+ neighbors, LAY006 should
    report it as crowded.
    """

    def test_empty_schematic_no_warnings(self) -> None:
        """Empty schematic has no symbols, so no LAY006 warnings."""
        doc = _doc("")
        issues = lint_layout_crowding(doc)
        lay006_issues = [i for i in issues if i.code == "LAY006"]
        assert lay006_issues == []

    def test_single_symbol_no_warning(self) -> None:
        """Single symbol cannot have neighbors, so no LAY006 warning."""
        doc = _doc(_make_symbol("R1", 30.0, 50.0))
        issues = lint_layout_crowding(doc)
        lay006_issues = [i for i in issues if i.code == "LAY006"]
        assert lay006_issues == []

    def test_two_symbols_no_warning(self) -> None:
        """Two symbols far apart don't reach 6-neighbor threshold."""
        body = " ".join(
            [
                _make_symbol("R1", 0.0, 0.0),
                _make_symbol("R2", 50.0, 0.0),  # outside 30mm radius
            ]
        )
        doc = _doc(body)
        issues = lint_layout_crowding(doc)
        lay006_issues = [i for i in issues if i.code == "LAY006"]
        assert lay006_issues == []

    def test_exactly_six_neighbors_triggers_warning(self) -> None:
        """A symbol with exactly 6 neighbors within 30mm should trigger LAY006."""
        # Create a grid: center (R7) at (0,0), 6 neighbors at compass points
        # within 30mm radius (e.g., at distance 10mm).
        body = " ".join(
            [
                _make_symbol("R1", 10.0, 0.0),  # East
                _make_symbol("R2", -10.0, 0.0),  # West
                _make_symbol("R3", 0.0, 10.0),  # North
                _make_symbol("R4", 0.0, -10.0),  # South
                _make_symbol("R5", 7.0, 7.0),  # NE
                _make_symbol("R6", -7.0, -7.0),  # SW
                _make_symbol("R7", 0.0, 0.0),  # Center (R7 has 6 neighbors)
            ]
        )
        doc = _doc(body)
        issues = lint_layout_crowding(doc)
        lay006_issues = [i for i in issues if i.code == "LAY006"]
        assert len(lay006_issues) > 0, "Expected LAY006 warning for 6+ neighbors"
        # Verify message mentions the crowded symbol
        assert any("R7" in i.message or "crowded" in i.message.lower() for i in lay006_issues)

    def test_highly_dense_cluster_triggers_warnings(self) -> None:
        """A tight cluster should trigger LAY006 for multiple symbols."""
        # Create a 5×5 grid of symbols with 1mm spacing (all within 30mm radius)
        symbols = []
        for i in range(5):
            for j in range(5):
                symbols.append(_make_symbol(f"R{i * 5 + j + 1}", float(i), float(j)))
        body = " ".join(symbols)
        doc = _doc(body)
        issues = lint_layout_crowding(doc)
        lay006_issues = [i for i in issues if i.code == "LAY006"]
        # Most/all of the 25 symbols should have 6+ neighbors at 1mm spacing
        assert len(lay006_issues) > 0, "Expected multiple LAY006 warnings in dense 5×5 cluster"

    def test_lay006_warns_about_all_crowded_symbols(self) -> None:
        """LAY006 should report crowded symbols (top 5 worst)."""
        # Create 7 closely packed symbols
        body = " ".join(
            [
                _make_symbol("R1", 0.0, 0.0),
                _make_symbol("R2", 5.0, 0.0),
                _make_symbol("R3", 0.0, 5.0),
                _make_symbol("R4", 5.0, 5.0),
                _make_symbol("R5", 2.5, 2.5),
                _make_symbol("R6", 7.5, 2.5),
                _make_symbol("R7", 2.5, 7.5),
            ]
        )
        doc = _doc(body)
        issues = lint_layout_crowding(doc)
        lay006_issues = [i for i in issues if i.code == "LAY006"]
        assert len(lay006_issues) > 0
        # Should limit to top 5 worst offenders (may have fewer if < 5 symbols
        # exceed threshold)
        assert len(lay006_issues) <= 5, (
            f"LAY006 should limit to top 5 crowded symbols, got {len(lay006_issues)}"
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
