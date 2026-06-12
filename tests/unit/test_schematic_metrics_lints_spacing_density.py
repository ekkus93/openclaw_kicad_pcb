"""Unit tests for schematic metrics: cluster detection, block spread, and LAY lint tests."""

from __future__ import annotations

import pytest

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.lint.sch import lint_layout_crowding
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.schematic_metrics import (
    compute_block_role_spread,
    detect_dense_clusters,
)
from kicad_pcb.sexpr import parse as _parse_sexpr

pytestmark = pytest.mark.unit

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
